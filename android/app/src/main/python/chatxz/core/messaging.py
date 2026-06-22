import threading, RNS, json, time, os, tempfile, uuid
from urllib import request as urlrequest

from chatxz.utils.helpers import format_speed
from chatxz.core.discovery import normalize_hash, message_dest_hash_for_identity
from chatxz.core.lan_rns import (
    build_announce_packet,
    clear_peer_path,
    detach_unhealthy_interfaces,
    interface_family,
    interface_is_healthy,
    online_interfaces,
    peer_path_entry,
    request_path_for_hash,
    request_paths_for_hash,
    scrub_peer_path,
    unicast_announce_packet,
    wait_for_peer_path,
)
from chatxz.utils.platform import is_android

APP_NAME = "chatxz"
LINK_CONNECT_TIMEOUT_S = 10
FAILOVER_CONNECT_TIMEOUT_S = 22
LINK_CONNECT_POLL_S = 0.1
IDENTITY_WAIT_TIMEOUT_S = 18
REVERSE_CONNECT_WAIT_S = 15
LINK_FAILOVER_GRACE_S = 12

MESSAGE_TYPE_TEXT = "text"
MESSAGE_TYPE_FILE = "file"
MESSAGE_TYPE_IMAGE = "image"
MESSAGE_TYPE_VOICE = "voice"
MESSAGE_TYPE_VIDEO = "video"
MESSAGE_TYPE_EMOJI = "emoji"
MESSAGE_TYPE_LONGTEXT = "longtext"

class ChatMessage:
    def __init__(self, msg_type, content, sender=None, timestamp=None, file_name=None, file_size=None, msg_id=None):
        self.msg_type = msg_type
        self.content = content
        self.sender = sender
        self.timestamp = timestamp or time.time()
        self.file_name = file_name
        self.file_size = file_size
        self.msg_id = msg_id or str(uuid.uuid4())[:12]

    def to_dict(self):
        d = {
            "type": self.msg_type,
            "content": self.content,
            "timestamp": self.timestamp,
            "msg_id": self.msg_id,
        }
        if self.sender:
            d["sender"] = self.sender
        if self.file_name:
            d["file_name"] = self.file_name
        if self.file_size:
            d["file_size"] = self.file_size
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(
            msg_type=d.get("type", MESSAGE_TYPE_TEXT),
            content=d.get("content", ""),
            sender=d.get("sender"),
            timestamp=d.get("timestamp", time.time()),
            file_name=d.get("file_name"),
            file_size=d.get("file_size"),
            msg_id=d.get("msg_id"),
        )

    def to_json(self):
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, data):
        return cls.from_dict(json.loads(data))

class MessagingBackend:
    def __init__(self, identity, config_dir, on_message=None, on_file=None,
                 on_progress=None, on_link_established=None, on_link_closed=None,
                 display_name="", auto_announce=False,
                 receive_dir=None, peer_resolver=None):
        self.identity = identity
        self.config_dir = config_dir
        self.receive_dir = receive_dir or os.path.join(config_dir, "received")
        self.on_message = on_message
        self.on_file = on_file
        self.on_progress = on_progress
        self.on_link_established = on_link_established
        self.on_link_closed = on_link_closed
        self.display_name = display_name
        self.auto_announce = auto_announce
        self.announce_interval = 15 if is_android() else 30
        self.destination = None
        self.links = {}
        self.active_link = None
        self.active_peer_hash = None
        self.running = False
        self.shutdown_requested = False
        self._announce_thread = None
        self._pending_files = {}
        self._pending_lock = threading.Lock()
        self.queue_file = os.path.join(config_dir, "queue.json")
        self.message_queue = self._load_queue()
        self._file_send_lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._sent_messages = {}
        self._receipt_callbacks = {}
        self._active_resources = {}
        self._cancel_events = {}
        self._file_handles = {}
        self._cancelled_transfers = set()
        self._current_transfer_id = None
        self._progress_last = {}
        self._progress_throttle_s = 0.25
        self.my_dest_hash = None
        self.identity_to_dest = {}
        self.dest_to_identity = {}
        self._send_link = None
        self.peer_resolver = peer_resolver
        self._link_peer_hashes = {}
        self._link_handoff = False
        self._last_handoff = False
        self._failover_last_attempt = 0
        self._failover_cooldown_s = 4
        self._failover_in_progress = False
        self._last_link_established_at = 0

    def _is_self_hash(self, h):
        clean = normalize_hash(h)
        if not clean:
            return False
        if self.my_dest_hash and clean == normalize_hash(self.my_dest_hash):
            return True
        try:
            if self.identity and clean == normalize_hash(RNS.hexrep(self.identity.hash)):
                return True
        except Exception:
            pass
        return False

    def _cache_link_peer(self, link, peer_hash):
        if link and peer_hash and peer_hash != "unknown" and not self._is_self_hash(peer_hash):
            self._link_peer_hashes[link.link_id] = normalize_hash(peer_hash)

    def _peer_for_link(self, link, fallback=None):
        cached = self._link_peer_hashes.get(link.link_id) if link else None
        if cached and not self._is_self_hash(cached):
            return self.dest_hash_for(cached)
        resolved = self._resolve_remote_peer(link, fallback=fallback)
        if self._is_self_hash(resolved):
            if self.active_peer_hash and not self._is_self_hash(self.active_peer_hash):
                return self.active_peer_hash
            if cached:
                return self.dest_hash_for(cached)
            return "unknown"
        if resolved and resolved != "unknown":
            self._cache_link_peer(link, resolved)
        return resolved

    def register_peer_mapping(self, dest_hash, identity_hash=None):
        dest = normalize_hash(dest_hash)
        if not dest:
            return
        if identity_hash:
            ident = normalize_hash(identity_hash)
            if ident and ident != dest:
                self.identity_to_dest[ident] = dest
                self.dest_to_identity[dest] = ident

    def dest_hash_for(self, any_hash):
        clean = normalize_hash(any_hash)
        if not clean:
            return ""
        if clean in self.dest_to_identity:
            return clean
        mapped = self.identity_to_dest.get(clean)
        if mapped:
            return mapped
        return clean

    def hashes_equivalent(self, hash_a, hash_b):
        a = self.dest_hash_for(hash_a)
        b = self.dest_hash_for(hash_b)
        if a and b and a == b:
            return True
        if not a or not b:
            return False
        for key in (hash_a, hash_b):
            clean = normalize_hash(key)
            if clean in self.identity_to_dest:
                other = self.dest_hash_for(hash_b if key == hash_a else hash_a)
                if other and self.identity_to_dest.get(clean) == other:
                    return True
        return False

    def peer_aliases_for(self, any_hash):
        canonical = self.dest_hash_for(any_hash)
        aliases = {canonical} if canonical else set()
        ident = self.dest_to_identity.get(canonical)
        if ident:
            aliases.add(ident)
        for ident_hex, dest in self.identity_to_dest.items():
            if dest == canonical:
                aliases.add(ident_hex)
        return sorted(h for h in aliases if h and h != "unknown")

    def _link_attached_interface(self, link):
        if not link:
            return None
        return getattr(link, "attached_interface", None)

    def _interface_healthy(self, iface):
        return interface_is_healthy(iface)

    def _interface_path_score(self, iface):
        if not self._interface_healthy(iface):
            return 0
        fam = interface_family(iface)
        if fam == "lan":
            return 100
        if fam == "serial":
            return 20
        if fam == "udp":
            return 80
        return 50

    def _link_interface_healthy(self, link):
        return self._interface_healthy(self._link_attached_interface(link))

    def _peer_has_path(self, dest_hash):
        clean = normalize_hash(dest_hash)
        if len(clean) != 32:
            return False
        scrub_peer_path(clean)
        _, path_iface = peer_path_entry(clean)
        return bool(path_iface and self._interface_healthy(path_iface))

    def _peer_path_interface(self, dest_hash):
        scrub_peer_path(dest_hash)
        _, path_iface = peer_path_entry(dest_hash)
        return path_iface

    def _interfaces_equivalent(self, iface_a, iface_b):
        if iface_a is None or iface_b is None:
            return False
        if iface_a is iface_b:
            return True
        return str(iface_a) == str(iface_b)

    def _has_online_family(self, family):
        ifaces = online_interfaces(family=family)
        if not ifaces:
            return False
        if family != "lan":
            return True
        for iface in ifaces:
            if type(iface).__name__ == "AutoInterfacePeer":
                return True
        for iface in ifaces:
            spawned = getattr(iface, "spawned_interfaces", None)
            if isinstance(spawned, dict) and spawned:
                return True
        try:
            from chatxz.utils.platform import lan_ip as _lan_ip
            return bool(_lan_ip())
        except Exception:
            return False

    def _preferred_failover_family(self, peer, attached=None):
        attached = attached or self._link_attached_interface(self.active_link)
        att_fam = interface_family(attached)
        if att_fam == "serial" and self._has_online_family("lan"):
            return "lan"
        if att_fam == "lan" and self._has_online_family("serial"):
            return "serial"
        path_iface = self._peer_path_interface(peer)
        if path_iface and self._interface_healthy(path_iface):
            fam = interface_family(path_iface)
            if fam != att_fam:
                return fam
        if self._has_online_family("lan"):
            return "lan"
        if self._has_online_family("serial"):
            return "serial"
        return None

    def _prepare_failover_path(self, peer, prefer_family=None):
        scrub_peer_path(peer)
        detached = detach_unhealthy_interfaces()
        if detached:
            print(f"[connect] Detached {detached} offline RNS interface(s)")
        clear_peer_path(peer)
        self._announce()
        request_paths_for_hash(peer, family=prefer_family)
        if prefer_family:
            path_iface = wait_for_peer_path(peer, family=prefer_family, timeout_s=10.0)
            if path_iface:
                print(f"[connect] Path ready on {type(path_iface).__name__} ({prefer_family})")
                return True
        path_iface = wait_for_peer_path(peer, family=None, timeout_s=6.0)
        if path_iface:
            print(f"[connect] Path ready on {type(path_iface).__name__}")
            return True
        print(f"[connect] Waiting for path to {peer[:16]}... (no {prefer_family or 'usable'} path yet)")
        return False

    def link_needs_failover(self):
        if not self.active_link or not self.active_peer_hash:
            return False, ""
        peer = self.dest_hash_for(self.active_peer_hash)
        if not peer or peer == "unknown":
            return False, ""

        attached = self._link_attached_interface(self.active_link)
        in_grace = (time.time() - self._last_link_established_at) < LINK_FAILOVER_GRACE_S

        if not self._link_interface_healthy(self.active_link):
            return True, f"link interface offline ({type(attached).__name__ if attached else 'none'})"

        path_iface = self._peer_path_interface(peer)
        att_fam = interface_family(attached)
        path_fam = interface_family(path_iface) if path_iface else ""

        if path_iface and attached and not self._interfaces_equivalent(path_iface, attached):
            if self._interface_healthy(path_iface):
                new_score = self._interface_path_score(path_iface)
                old_score = self._interface_path_score(attached)
                if path_fam != att_fam:
                    if not in_grace or new_score > old_score:
                        return True, f"path moved to {path_fam} (link on {att_fam})"
                elif new_score > old_score + 15:
                    return True, f"better path on {type(path_iface).__name__}"

        if att_fam == "lan" and not self._has_online_family("lan") and self._has_online_family("serial"):
            return True, "LAN down, serial available"

        if att_fam == "serial" and not self._has_online_family("serial") and self._has_online_family("lan"):
            return True, "serial down, LAN available"

        if not self._peer_has_path(peer):
            alt = self._preferred_failover_family(peer, attached)
            if alt and self._has_online_family(alt):
                return True, f"path lost, trying {alt}"

        try:
            if getattr(self.active_link, "status", None) == RNS.Link.STALE:
                inactive = self.active_link.inactive_for()
                if inactive > 8:
                    return True, f"link stale ({inactive:.0f}s idle)"
        except Exception:
            pass

        return False, ""

    def _link_path_score(self, link):
        if not link:
            return 0
        if not self._link_interface_healthy(link):
            return 0
        try:
            iface = (
                self._link_attached_interface(link)
                or getattr(link, "interface", None)
                or getattr(link, "parent_interface", None)
            )
            fam = interface_family(iface)
            if fam == "serial":
                score = 20
            elif fam == "lan":
                score = 100
            elif fam == "udp":
                score = 80
            else:
                score = 50
            rtt = getattr(link, "rtt", None)
            if rtt is not None:
                try:
                    score = max(score, int(100 - min(float(rtt) * 8, 95)))
                except Exception:
                    pass
            return score
        except Exception:
            return 50

    def _resolve_incoming_link_peer(self, link, peer_hash):
        peer_hash = self.dest_hash_for(peer_hash)
        if peer_hash and peer_hash != "unknown" and not self._is_self_hash(peer_hash):
            return peer_hash
        if self.peer_resolver:
            try:
                ident_hex = ""
                computed_dest = ""
                ident = link.get_remote_identity()
                if ident and hasattr(ident, "hash") and ident.hash:
                    ident_hex = normalize_hash(RNS.hexrep(ident.hash))
                    computed_dest = self._dest_hash_from_identity(ident)
                fixed = self.peer_resolver(
                    ident_hex=ident_hex,
                    computed_dest=computed_dest,
                    link=link,
                )
                if fixed and not self._is_self_hash(fixed):
                    return self.dest_hash_for(fixed)
            except Exception as e:
                print(f"[messaging] incoming peer resolve fallback: {e}")
        resolved = self._resolve_remote_peer(link)
        if resolved and resolved != "unknown" and not self._is_self_hash(resolved):
            return self.dest_hash_for(resolved)
        if self.active_peer_hash and not self._is_self_hash(self.active_peer_hash):
            if self._incoming_matches_active_session(link):
                return self.dest_hash_for(self.active_peer_hash)
        return peer_hash or "unknown"

    def _incoming_matches_active_session(self, link):
        if not self.active_peer_hash or not self.active_link:
            return False
        try:
            ident = link.get_remote_identity()
            if ident:
                computed_dest = self._dest_hash_from_identity(ident)
                if computed_dest and self.hashes_equivalent(computed_dest, self.active_peer_hash):
                    return True
                ident_hex = normalize_hash(RNS.hexrep(ident.hash))
                if ident_hex and self.hashes_equivalent(ident_hex, self.active_peer_hash):
                    return True
        except Exception:
            pass
        return link.link_id != self.active_link.link_id

    def _handoff_to_link(self, link, peer_hash):
        peer_hash = self.dest_hash_for(peer_hash)
        old = self.active_link
        self._link_handoff = True
        self._last_handoff = True
        try:
            print(
                f"[messaging] Path switch to {peer_hash[:16]} "
                f"(score {self._link_path_score(link)} vs {self._link_path_score(old)})"
            )
            self._setup_link(link)
            self._cache_link_peer(link, peer_hash)
            self._notify_link_established(link, peer_hash)
            self._send_link = link
            if old and old.link_id != link.link_id:
                try:
                    old.teardown()
                except Exception:
                    pass
            self.drain_queue(link, peer_hash)
        finally:
            self._link_handoff = False

    def _outgoing_link(self):
        return self._send_link or self.active_link

    def _load_queue(self):
        try:
            with open(self.queue_file) as f:
                return json.load(f)
        except:
            return []

    def _save_queue(self):
        try:
            with open(self.queue_file, "w") as f:
                json.dump(self.message_queue, f, indent=2)
        except:
            pass

    def enqueue(self, msg_type, content, target_hash=None, file_name=None, file_size=None, file_path=None):
        entry = {
            "type": msg_type,
            "content": content,
            "target_hash": target_hash,
            "file_name": file_name,
            "file_size": file_size,
            "file_path": file_path,
            "timestamp": time.time(),
        }
        self.message_queue.append(entry)
        self._save_queue()
        print(f"[queue] Enqueued {msg_type} for target {target_hash[:16] if target_hash else 'any (next peer)'}")

    def drain_queue(self, link, target_hash):
        remaining = []
        sent = 0
        for entry in self.message_queue:
            tgt = entry.get("target_hash")
            if tgt is None or tgt == "" or tgt == target_hash:
                try:
                    if entry["type"] in ("text", "emoji"):
                        if self.send_message(entry["content"]):
                            sent += 1
                    elif entry["type"] in ("file", "image", "video", "voice"):
                        fp = entry.get("file_path") or entry.get("content")
                        if fp and os.path.exists(fp):
                            result = self.send_file(fp, entry["type"])
                            if result:
                                sent += 1
                        else:
                            print(f"[queue] File no longer exists: {fp}")
                            remaining.append(entry)
                except Exception as e:
                    print(f"[queue] Failed to send: {e}")
                    remaining.append(entry)
            else:
                remaining.append(entry)
        if sent:
            print(f"[queue] Drained {sent} queued items for {target_hash[:16] if target_hash else 'peer'}...")
        self.message_queue = remaining
        self._save_queue()

    def queue_size(self):
        return len(self.message_queue)

    def start(self):
        self.destination = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            APP_NAME,
            "messages"
        )
        self.destination.set_proof_strategy(RNS.Destination.PROVE_ALL)
        self.destination.accepts_links(True)
        self.destination.set_link_established_callback(self._link_callback)

        if self.auto_announce:
            self._announce()
            self._announce_thread = threading.Thread(target=self._announce_loop, daemon=True)
            self._announce_thread.start()

        self.running = True
        print(f"[messaging] Started (auto_announce={self.auto_announce})")
        return self.destination

    def announce(self):
        self._announce()

    def _announce(self, peer_ip=None, unicast_subnet=None):
        if not self.destination:
            return
        announce_data = json.dumps({
            "app": APP_NAME,
            "name": self.display_name or ""
        }).encode("utf-8")
        self.destination.announce(app_data=announce_data)
        if unicast_subnet is None:
            unicast_subnet = is_android()
        if peer_ip or unicast_subnet:
            packet = build_announce_packet(self.destination, announce_data)
            sent = unicast_announce_packet(
                packet,
                peer_ip=peer_ip,
                subnet_probe=unicast_subnet,
            )
            if sent:
                hint = f" + {sent} unicast" if sent else ""
                print(f"[messaging] Announced on LAN (name={self.display_name or 'none'}{hint})")
                return
        print(f"[messaging] Announced on LAN (name={self.display_name or 'none'})")

    def _request_peer_connect(self, peer_ip, peer_port, my_hash, caller_ip=None, caller_port=8742):
        """Ask peer to open outbound RNS link (fixes Android inbound UDP link requests)."""
        if not peer_ip:
            return False
        port = int(peer_port or 8742)
        payload = {
            "hash": normalize_hash(my_hash or self.my_dest_hash or ""),
            "ip": caller_ip or "",
            "port": int(caller_port or 8742),
        }
        url = f"http://{peer_ip}:{port}/api/request_connect"
        try:
            req = urlrequest.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlrequest.urlopen(req, timeout=3.0) as resp:
                return 200 <= resp.status < 300
        except Exception as exc:
            print(f"[connect] Reverse-connect request to {peer_ip} failed: {exc}")
            return False

    def _wait_for_reverse_link(self, dest_hex, alt_hex=None, timeout_s=REVERSE_CONNECT_WAIT_S):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._interrupted():
                return False
            if self.active_link and self.active_peer_hash:
                if self.hashes_equivalent(dest_hex, self.active_peer_hash):
                    return True
                if alt_hex and self.hashes_equivalent(alt_hex, self.active_peer_hash):
                    return True
            time.sleep(LINK_CONNECT_POLL_S)
        return False

    def _announce_loop(self):
        while self.running:
            for _ in range(self.announce_interval):
                if not self.running:
                    return
                time.sleep(1)
            self._announce()

    def stop(self):
        self.running = False
        for link_id, link in self.links.items():
            try:
                link.teardown()
            except:
                pass

    def _dest_hash_from_identity(self, ident):
        dest = message_dest_hash_for_identity(ident)
        if dest and ident and getattr(ident, "hash", None):
            ident_hex = normalize_hash(RNS.hexrep(ident.hash))
            if ident_hex and ident_hex != dest:
                self.register_peer_mapping(dest, ident_hex)
        return dest

    def _identity_for_hash(self, hash_hex):
        clean = normalize_hash(hash_hex)
        if len(clean) != 32:
            return None
        try:
            raw = bytes.fromhex(clean)
        except Exception:
            return None
        ident = RNS.Identity.recall(raw)
        if ident is None:
            ident = RNS.Identity.recall(raw, from_identity_hash=True)
        return ident

    def _hash_from_peer_info(self, peer_info):
        if not peer_info:
            return ""
        for key in ("hash", "identity_hash"):
            candidate = normalize_hash(peer_info.get(key))
            if not candidate or len(candidate) != 32:
                continue
            ident = self._identity_for_hash(candidate)
            if ident:
                dest = message_dest_hash_for_identity(ident)
                if dest:
                    self.register_peer_mapping(dest, normalize_hash(RNS.hexrep(ident.hash)))
                    return dest
        return normalize_hash(peer_info.get("hash"))

    def _wait_for_identity(self, hash_hex, peer_ip=None, peer_port=None, peer_lookup=None,
                          caller_ip=None, caller_port=8742):
        clean = normalize_hash(hash_hex)
        deadline = time.time() + IDENTITY_WAIT_TIMEOUT_S
        last_log = 0
        while time.time() < deadline:
            ident = self._identity_for_hash(clean)
            if ident:
                return ident, clean

            if peer_lookup:
                peer = peer_lookup(peer_ip, clean)
                if peer:
                    alt = self._hash_from_peer_info(peer)
                    if alt and alt != clean:
                        clean = alt
                        ident = self._identity_for_hash(clean)
                        if ident:
                            print(f"[connect] Resolved peer via discovery: {clean[:16]}...")
                            return ident, clean
                    if peer.get("via") == "rns":
                        alt = normalize_hash(peer.get("hash"))
                        if alt:
                            clean = alt
                            ident = self._identity_for_hash(clean)
                            if ident:
                                return ident, clean

            now = time.time()
            if now - last_log >= 3:
                remaining = int(deadline - now)
                print(f"[connect] Waiting for peer identity ({remaining}s left)...")
                last_log = now
            request_path_for_hash(clean)
            time.sleep(0.5)

        return None, clean

    def _resolve_remote_peer(self, link, fallback=None):
        ident_hex = ""
        computed_dest = ""
        try:
            ident = link.get_remote_identity()
            if ident and hasattr(ident, "hash") and ident.hash:
                ident_hex = normalize_hash(RNS.hexrep(ident.hash))
                computed_dest = self._dest_hash_from_identity(ident)
                if not computed_dest:
                    pub = ident.get_public_key()
                    if pub:
                        with RNS.Identity.known_destinations_lock:
                            for dest_hash_bytes, entry in RNS.Identity.known_destinations.items():
                                if len(entry) > 2 and entry[2] == pub:
                                    computed_dest = normalize_hash(RNS.hexrep(dest_hash_bytes))
                                    self.register_peer_mapping(computed_dest, ident_hex)
                                    break
        except Exception:
            pass

        if self.peer_resolver:
            try:
                resolved = self.peer_resolver(
                    ident_hex=ident_hex,
                    computed_dest=computed_dest,
                    fallback=fallback,
                    link=link,
                )
                if resolved:
                    return self.dest_hash_for(resolved)
            except Exception as e:
                print(f"[messaging] peer_resolver error: {e}")

        if computed_dest:
            return self.dest_hash_for(computed_dest)
        if fallback:
            return self.dest_hash_for(fallback)
        if ident_hex and not self._is_self_hash(ident_hex):
            return self.dest_hash_for(ident_hex)
        return "unknown"

    def _get_remote_hash(self, link):
        return self._peer_for_link(link)

    def _peer_destination_hash(self, link, fallback=None):
        return self._peer_for_link(link, fallback=fallback)

    def _notify_link_established(self, link, peer_hash=None):
        peer = self.dest_hash_for(peer_hash or self._peer_destination_hash(link))
        if not peer or peer == "unknown":
            peer = self.dest_hash_for(self.active_peer_hash or "")
        if not peer or peer == "unknown":
            return
        self.active_link = link
        self.active_peer_hash = peer
        self._last_link_established_at = time.time()
        if self._send_link is None:
            self._send_link = link
        if self.on_link_established:
            try:
                self.on_link_established(peer, link)
            except Exception as e:
                print(f"[messaging] on_link_established error: {e}")

    def _setup_link(self, link):
        self.links[link.link_id] = link
        link.set_link_closed_callback(self._link_closed(link))
        link.set_packet_callback(self._packet_callback(link))
        try:
            link.set_resource_strategy(RNS.Link.ACCEPT_ALL)
            link.set_resource_concluded_callback(self._resource_concluded(link))
            print(f"[messaging] Resource strategy set to ACCEPT_ALL for link {link.link_id.hex()[:12]}")
        except Exception as e:
            print(f"[messaging] Failed to set resource strategy: {e}")

    def _send_receipt(self, link, msg_id, status):
        try:
            receipt = json.dumps({"msg_id": msg_id, "status": status})
            msg = ChatMessage("__receipt", receipt)
            packet = RNS.Packet(link, msg.to_json().encode("utf-8"))
            packet.send()
        except:
            pass

    def send_read_receipt(self, link, msg_id):
        try:
            receipt = json.dumps({"msg_id": msg_id})
            msg = ChatMessage("__read_receipt", receipt)
            packet = RNS.Packet(link, msg.to_json().encode("utf-8"))
            packet.send()
        except:
            pass

    def _link_callback(self, link):
        peer_hash = self._resolve_incoming_link_peer(link, self._peer_destination_hash(link))
        self._cache_link_peer(link, peer_hash)

        if self.active_link and self.active_peer_hash:
            same_peer = (
                self.hashes_equivalent(peer_hash, self.active_peer_hash)
                or (peer_hash == "unknown" and self._incoming_matches_active_session(link))
            )
            if same_peer:
                if peer_hash == "unknown":
                    peer_hash = self.dest_hash_for(self.active_peer_hash)
                    self._cache_link_peer(link, peer_hash)
                if link.link_id == self.active_link.link_id:
                    print(f"[messaging] Ignoring duplicate incoming link from {peer_hash[:16]}...")
                    try:
                        link.teardown()
                    except Exception:
                        pass
                    return
                if self._link_path_score(link) >= self._link_path_score(self.active_link):
                    self._handoff_to_link(link, peer_hash)
                else:
                    print(f"[messaging] Keeping current link (better path than incoming {peer_hash[:16]}...)")
                    try:
                        link.teardown()
                    except Exception:
                        pass
                return

        if self._session_occupied(peer_hash):
            if peer_hash == "unknown" and self._incoming_matches_active_session(link):
                peer_hash = self.dest_hash_for(self.active_peer_hash)
                self._handoff_to_link(link, peer_hash)
                return
            print(
                f"[messaging] Rejecting incoming link from {peer_hash[:16]}... "
                f"(busy with {self.active_peer_hash[:16]}...)"
            )
            try:
                link.teardown()
            except Exception:
                pass
            return

        print(f"[messaging] Incoming link established: {link.link_id.hex()[:12]}")
        self._last_handoff = False
        self._setup_link(link)
        self._notify_link_established(link, peer_hash)
        self.drain_queue(link, peer_hash)

    def _link_closed(self, link):
        def callback(link):
            if link.link_id in self.links:
                del self.links[link.link_id]
            self._link_peer_hashes.pop(link.link_id, None)
            closing_active = self.active_link and self.active_link.link_id == link.link_id
            if closing_active:
                if self._link_handoff:
                    pass
                else:
                    self.active_link = None
                    self.active_peer_hash = None
            if self._send_link and self._send_link.link_id == link.link_id:
                self._send_link = self.active_link
            if self.on_link_closed and not self._link_handoff:
                remote_hash = self.dest_hash_for(self._peer_for_link(link))
                try:
                    self.on_link_closed(remote_hash, handoff=closing_active and bool(self.active_link))
                except TypeError:
                    try:
                        self.on_link_closed(remote_hash)
                    except Exception as e:
                        print(f"[messaging] on_link_closed error: {e}")
                except Exception as e:
                    print(f"[messaging] on_link_closed error: {e}")
        return callback

    def _packet_callback(self, link):
        def callback(message, packet):
            try:
                chat_msg = ChatMessage.from_json(message.decode("utf-8"))
                remote_hash = self.dest_hash_for(self._peer_for_link(link))

                if chat_msg.msg_type == "__receipt":
                    try:
                        receipt = json.loads(chat_msg.content)
                        msg_id = receipt.get("msg_id")
                        status = receipt.get("status", "received")
                        cb = self._receipt_callbacks.pop(msg_id, None)
                        if cb:
                            cb(status, receipt)
                        print(f"[receipt] Received {status} for msg {msg_id[:8]} from {remote_hash[:16]}")
                    except Exception as e:
                        print(f"[receipt] Error: {e}")
                    return

                if chat_msg.msg_type == "__read_receipt":
                    try:
                        receipt = json.loads(chat_msg.content)
                        msg_id = receipt.get("msg_id")
                        cb = self._receipt_callbacks.pop(msg_id, None)
                        if cb:
                            cb("read", receipt)
                        print(f"[receipt] Read receipt for msg {msg_id[:8]} from {remote_hash[:16]}")
                    except Exception as e:
                        print(f"[receipt] Read receipt error: {e}")
                    return

                chat_msg.sender = remote_hash
                print(f"[messaging] Received {chat_msg.msg_type} from {remote_hash[:16]}...")

                if chat_msg.msg_type in (MESSAGE_TYPE_FILE, MESSAGE_TYPE_IMAGE, MESSAGE_TYPE_VIDEO, MESSAGE_TYPE_VOICE, MESSAGE_TYPE_LONGTEXT):
                    with self._pending_lock:
                        queue = self._pending_files.setdefault(link.link_id, [])
                        queue.append(chat_msg)
                    print(f"[messaging] Waiting for resource data for {chat_msg.file_name}...")
                    self._emit_progress(
                        chat_msg.file_name or "file",
                        0,
                        total_size=chat_msg.file_size or 0,
                        direction="receive",
                        transfer_id=chat_msg.msg_id,
                        status="active",
                    )
                elif self.on_message:
                    self.on_message(chat_msg, remote_hash)

                if chat_msg.msg_type in (MESSAGE_TYPE_TEXT, MESSAGE_TYPE_EMOJI):
                    self._send_receipt(link, chat_msg.msg_id, "received")
            except Exception as e:
                print(f"[messaging] Packet callback error: {e}")
                if self.on_message:
                    self.on_message(
                        ChatMessage("system", f"Failed to parse message: {e}"),
                        None
                    )
        return callback

    def _dequeue_pending_file(self, link_id, resource=None):
        with self._pending_lock:
            queue = self._pending_files.get(link_id, [])
            if queue:
                return queue.pop(0)
        for _ in range(20):
            time.sleep(0.05)
            with self._pending_lock:
                queue = self._pending_files.get(link_id, [])
                if queue:
                    return queue.pop(0)
        fname = None
        if resource is not None:
            for attr in ("name", "title", "file_name"):
                val = getattr(resource, attr, None)
                if val:
                    fname = os.path.basename(str(val))
                    break
            spath = getattr(resource, "storagepath", None)
            if not fname and spath:
                fname = os.path.basename(str(spath))
        msg_type = MESSAGE_TYPE_FILE
        if fname:
            from chatxz.utils.helpers import media_type_for_filename
            msg_type = media_type_for_filename(fname)
        return ChatMessage(msg_type, "", file_name=fname or f"file_{int(time.time())}")

    def _resource_concluded(self, link):
        def callback(resource):
            try:
                print(f"[messaging] Resource concluded, status={resource.status}")
                if resource.status == RNS.Resource.COMPLETE:
                    chat_msg = self._dequeue_pending_file(link.link_id, resource)

                    os.makedirs(self.receive_dir, exist_ok=True)
                    fname = chat_msg.file_name or f"file_{int(time.time())}"
                    save_path = os.path.join(self.receive_dir, fname)

                    if hasattr(resource, 'data') and resource.data is not None:
                        if hasattr(resource.data, 'read'):
                            data = resource.data.read()
                        else:
                            data = resource.data
                        with open(save_path, "wb") as f:
                            f.write(data)
                        print(f"[messaging] File saved to {save_path}")
                    elif hasattr(resource, 'storagepath') and os.path.exists(resource.storagepath):
                        import shutil
                        shutil.copy2(resource.storagepath, save_path)
                        print(f"[messaging] File copied from storage to {save_path}")
                    else:
                        print(f"[messaging] No data available in resource")
                        return

                    if chat_msg.msg_type == MESSAGE_TYPE_LONGTEXT:
                        try:
                            with open(save_path, "r", encoding="utf-8") as f:
                                long_text = f.read()
                            chat_msg.msg_type = MESSAGE_TYPE_TEXT
                            chat_msg.content = long_text
                            os.unlink(save_path)
                        except Exception as e:
                            print(f"[messaging] Failed to read long text: {e}")
                    else:
                        chat_msg.content = save_path
                    remote_hash = self.dest_hash_for(self._peer_for_link(link))
                    if self.on_message:
                        self.on_message(chat_msg, remote_hash)
                    self._emit_progress(
                        chat_msg.file_name or "file",
                        100,
                        total_size=chat_msg.file_size or 0,
                        direction="receive",
                        transfer_id=chat_msg.msg_id,
                        status="complete",
                    )
                    self._send_receipt(link, chat_msg.msg_id, "received")
                else:
                    print(f"[messaging] Resource transfer failed (status={resource.status})")
                    with self._pending_lock:
                        queue = self._pending_files.get(link.link_id, [])
                        chat_msg = queue.pop(0) if queue else None
                    if chat_msg:
                        self._emit_progress(
                            chat_msg.file_name or "file",
                            0,
                            direction="receive",
                            transfer_id=chat_msg.msg_id,
                            status="failed",
                        )
                    if chat_msg and self.on_message:
                        self.on_message(
                            ChatMessage("system", f"File transfer failed: {chat_msg.file_name}"),
                            self.dest_hash_for(self._peer_for_link(link))
                        )
            except Exception as e:
                print(f"[messaging] Resource concluded error: {e}")
        return callback

    def _emit_progress(self, file_name, progress, total_size=0, speed="", direction="receive", transfer_id=None, status="active"):
        if transfer_id and transfer_id in self._cancelled_transfers and status == "active":
            return
        if status in ("complete", "cancelled", "failed"):
            self._progress_last.pop(transfer_id or file_name, None)
        elif status == "active":
            key = transfer_id or file_name or "default"
            now = time.time()
            last = self._progress_last.get(key, {})
            if last and (now - last.get("ts", 0)) < self._progress_throttle_s:
                if abs(progress - last.get("pct", -1)) < 1:
                    return
            self._progress_last[key] = {"ts": now, "pct": progress}
        if self.on_progress:
            try:
                self.on_progress({
                    "file_name": file_name,
                    "progress": progress,
                    "size": total_size,
                    "speed": speed,
                    "direction": direction,
                    "transfer_id": transfer_id,
                    "status": status,
                })
            except Exception as e:
                print(f"[progress] callback error: {e}")

    def _resolve_transfer_id(self, transfer_id=None, file_name=None):
        tid = transfer_id or self._current_transfer_id
        if tid and tid in self._active_resources:
            return tid
        if tid:
            return tid
        if file_name:
            for rid in list(self._active_resources.keys()):
                msg = self._sent_messages.get(rid)
                if msg and getattr(msg, "file_name", None) == file_name:
                    return rid
        return tid

    def _cleanup_transfer(self, transfer_id):
        self._active_resources.pop(transfer_id, None)
        self._cancel_events.pop(transfer_id, None)
        fh = self._file_handles.pop(transfer_id, None)
        if fh:
            try:
                fh.close()
            except Exception:
                pass

    def cancel_transfer(self, transfer_id=None, file_name=None):
        cancelled = False
        tid = self._resolve_transfer_id(transfer_id, file_name)
        if not tid:
            return False
        self._cancelled_transfers.add(tid)
        cancel_ev = self._cancel_events.get(tid)
        if cancel_ev:
            cancel_ev.set()
            cancelled = True
        targets = [(rid, res) for rid, res in self._active_resources.items() if rid == tid]
        if not targets and file_name:
            for rid, res in list(self._active_resources.items()):
                msg = self._sent_messages.get(rid)
                if msg and getattr(msg, "file_name", None) == file_name:
                    targets.append((rid, res))
                    tid = rid
                    self._cancelled_transfers.add(tid)
                    ev = self._cancel_events.get(tid)
                    if ev:
                        ev.set()
        for rid, resource in targets:
            try:
                if hasattr(resource, "cancel"):
                    resource.cancel()
                elif hasattr(resource, "close"):
                    resource.close()
                cancelled = True
                print(f"[transfer] Cancelled resource {rid}")
            except Exception as e:
                print(f"[transfer] cancel resource {rid}: {e}")
            self._cleanup_transfer(rid)
        if cancelled or tid in self._cancelled_transfers:
            fname = file_name or ""
            msg = self._sent_messages.get(tid)
            if msg and getattr(msg, "file_name", None):
                fname = msg.file_name
            if not fname:
                for entry in reversed(self.message_queue):
                    if entry.get("msg_id") == tid:
                        fname = entry.get("file_name", "")
                        break
            self._emit_progress(fname, 0, status="cancelled", direction="send", transfer_id=tid)
        if self._current_transfer_id == tid:
            self._current_transfer_id = None
        return cancelled

    def _session_occupied(self, peer_hash):
        if not self.active_link or not self.active_peer_hash:
            return False
        return not self.hashes_equivalent(peer_hash, self.active_peer_hash)

    def _teardown_active_link(self, preserve_peer=False, handoff=False):
        self._link_handoff = handoff
        try:
            if self.active_link:
                try:
                    self.active_link.teardown()
                except Exception:
                    pass
            self.active_link = None
            self._send_link = None
            if not preserve_peer:
                self.active_peer_hash = None
                self._link_peer_hashes.clear()
        finally:
            if handoff:
                self._link_handoff = False

    def reconnect_active_peer(self, peer_ip=None, peer_port=None, peer_lookup=None,
                              caller_ip=None, caller_port=8742, reason=""):
        now = time.time()
        if self._failover_in_progress:
            return False
        if now - self._failover_last_attempt < self._failover_cooldown_s:
            return False
        peer = self.dest_hash_for(self.active_peer_hash or "")
        if not peer or peer == "unknown":
            return False

        self._failover_last_attempt = now
        self._failover_in_progress = True
        try:
            prefer = self._preferred_failover_family(peer)
            print(f"[connect] Failover reconnect to {peer[:16]}... ({reason})")
            self._teardown_active_link(preserve_peer=True, handoff=True)
            time.sleep(0.3)
            if not self._prepare_failover_path(peer, prefer_family=prefer):
                return False
            if peer_ip:
                self._request_peer_connect(
                    peer_ip, int(peer_port or 8742),
                    normalize_hash(self.my_dest_hash or ""),
                    caller_ip=caller_ip, caller_port=int(caller_port or 8742),
                )
            return self.connect_to(
                peer,
                peer_ip,
                peer_port,
                peer_lookup,
                caller_ip,
                caller_port,
                replace=False,
                failover=True,
            )
        finally:
            self._failover_in_progress = False

    def _interrupted(self):
        return self.shutdown_requested or not self.running

    def connect_to(self, destination_hash_hex, peer_ip=None, peer_port=None, peer_lookup=None,
                   caller_ip=None, caller_port=8742, replace=False, failover=False):
        with self._connect_lock:
            if self._interrupted():
                return False

            clean = normalize_hash(destination_hash_hex)
            if len(clean) != 32:
                print(f"[connect] Invalid hash length ({len(clean)} chars, expected 32)")
                return False

            old_link = None
            if self.active_link and self.active_peer_hash and self.hashes_equivalent(clean, self.active_peer_hash):
                link_ok = self._link_interface_healthy(self.active_link) and self._peer_has_path(clean)
                if not replace:
                    if link_ok:
                        print(f"[connect] Already connected to {self.active_peer_hash[:16]}...")
                        return True
                    print(f"[connect] Stale link to {self.active_peer_hash[:16]}... — reconnecting")
                    self._teardown_active_link(preserve_peer=True, handoff=True)
                elif self._link_path_score(self.active_link) >= 90 and link_ok:
                    return True
                else:
                    old_link = self.active_link
                    self._teardown_active_link(preserve_peer=True, handoff=True)
                    print(f"[connect] Replacing link to {self.active_peer_hash[:16]} for better path...")
            elif (
                self.active_link and self.active_peer_hash
                and not self.hashes_equivalent(clean, self.active_peer_hash)
            ):
                old_link = self.active_link

            known_identity = self._identity_for_hash(clean)
            if known_identity is None:
                known_identity, clean = self._wait_for_identity(
                    clean,
                    peer_ip=peer_ip,
                    peer_port=peer_port,
                    peer_lookup=peer_lookup,
                    caller_ip=caller_ip,
                    caller_port=caller_port,
                )
            if known_identity is None:
                print(f"[connect] No known identity for {clean[:16]}...")
                print("[connect] Peer identity not learned yet (beacon pubkey or RNS announce).")
                if peer_ip:
                    print(f"[connect] Ensure chatxz is open on {peer_ip} and try Announce in the UI.")
                else:
                    print("[connect] On the peer device: open chatxz, wait ~15s, or tap Announce.")
                return False

            ident_hex = normalize_hash(RNS.hexrep(known_identity.hash))
            try:
                destination = RNS.Destination(
                    known_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    APP_NAME,
                    "messages"
                )
            except Exception as e:
                print(f"[connect] Destination creation failed: {e}")
                return False

            dest_hex = normalize_hash(RNS.hexrep(destination.hash))
            self.register_peer_mapping(dest_hex, ident_hex)

            my_hash = normalize_hash(self.my_dest_hash or dest_hex)
            if peer_ip:
                self._request_peer_connect(
                    peer_ip, peer_port, my_hash,
                    caller_ip=caller_ip, caller_port=caller_port,
                )
            scrub_peer_path(dest_hex)
            request_paths_for_hash(dest_hex)
            connect_timeout = FAILOVER_CONNECT_TIMEOUT_S if failover else LINK_CONNECT_TIMEOUT_S
            print(f"[connect] Connecting to {dest_hex[:16]}... (timeout {connect_timeout}s)")

            link = None
            try:
                link = RNS.Link(destination)
                deadline = time.time() + connect_timeout
                while time.time() < deadline:
                    if self._interrupted():
                        print("[connect] Aborted (shutdown)")
                        try:
                            link.teardown()
                        except Exception:
                            pass
                        return False
                    time.sleep(LINK_CONNECT_POLL_S)
                    try:
                        if link.status == RNS.Link.ACTIVE:
                            if old_link and old_link.link_id != link.link_id:
                                self._link_handoff = True
                                try:
                                    old_link.teardown()
                                except Exception:
                                    pass
                                finally:
                                    self._link_handoff = False
                            self._last_handoff = bool(old_link)
                            self._setup_link(link)
                            self._cache_link_peer(link, dest_hex)
                            self._notify_link_established(link, dest_hex)
                            self._send_link = link
                            try:
                                link.identify(self.identity)
                            except Exception:
                                pass
                            print("[connect] Link established")
                            self.drain_queue(link, dest_hex)
                            return True
                        if link.status == RNS.Link.CLOSED:
                            break
                    except Exception:
                        pass
                    if self.active_link and link and self.active_link.link_id == link.link_id:
                        print("[connect] Link established")
                        return True
            except Exception as e:
                print(f"[connect] Link failed: {e}")
            finally:
                if link:
                    try:
                        if link.status != RNS.Link.ACTIVE:
                            link.teardown()
                    except Exception:
                        pass
                    if link.link_id in self.links:
                        del self.links[link.link_id]

            if peer_ip:
                print("[connect] Outbound link timed out — waiting for reverse connect...")
                self._request_peer_connect(
                    peer_ip, peer_port, my_hash,
                    caller_ip=caller_ip, caller_port=caller_port,
                )
                if self._wait_for_reverse_link(dest_hex, alt_hex=clean):
                    print("[connect] Reverse connect established")
                    return True

            print("[connect] Peer not reachable")
            return False

    def send_message(self, text, receipt_callback=None):
        link = self._outgoing_link()
        if not link:
            print("[messaging] send_message: no active link")
            return False
        msg = ChatMessage(MESSAGE_TYPE_TEXT, text)
        data = msg.to_json().encode("utf-8")
        mtu = getattr(link, 'mtu', 500)
        try:
            if len(data) > mtu - 50:
                return self._send_long_text(msg, text, data, receipt_callback, link)
            packet = RNS.Packet(link, data)
            packet.send()
            print(f"[messaging] Sent text message: {text[:50]}...")
            self._sent_messages[msg.msg_id] = msg
            if receipt_callback:
                self._receipt_callbacks[msg.msg_id] = receipt_callback
            return msg
        except Exception as e:
            print(f"[messaging] Send failed: {e}")
            return False

    def _send_long_text(self, msg, text, data, receipt_callback, link=None):
        link = link or self._outgoing_link()
        import tempfile as _tf
        tmp = _tf.NamedTemporaryFile(delete=False, suffix=".txt", mode="w")
        tmp.write(text)
        tmp_path = tmp.name
        tmp.close()
        meta = ChatMessage(MESSAGE_TYPE_LONGTEXT, json.dumps({"msg_id": msg.msg_id, "file_name": "longtext.txt"}))
        try:
            packet = RNS.Packet(link, meta.to_json().encode("utf-8"))
            packet.send()
        except Exception as e:
            print(f"[messaging] Long text metadata send failed: {e}")
            os.unlink(tmp_path)
            return False
        try:
            f = open(tmp_path, "rb")
            RNS.Resource(f, link, callback=self._resource_send_callback("longtext"),
                         progress_callback=None, auto_compress=True)
            print(f"[messaging] Sent long text: {text[:50]}... ({len(data)} bytes as resource)")
            self._sent_messages[msg.msg_id] = msg
            if receipt_callback:
                self._receipt_callbacks[msg.msg_id] = receipt_callback
            os.unlink(tmp_path)
            return msg
        except Exception as e:
            print(f"[messaging] Long text resource send failed: {e}")
            os.unlink(tmp_path)
            return False

    def send_file(self, file_path, msg_type=MESSAGE_TYPE_FILE, progress_callback=None, transfer_id=None):
        link = self._outgoing_link()
        if not link or not os.path.exists(file_path):
            return False
        with self._file_send_lock:
            fname = os.path.basename(file_path)
            fsize = os.path.getsize(file_path)
            chat_msg = ChatMessage(msg_type, str(time.time()), file_name=fname, file_size=fsize, msg_id=transfer_id)
            transfer_id = chat_msg.msg_id
            self._current_transfer_id = transfer_id
            cancel_ev = threading.Event()
            self._cancel_events[transfer_id] = cancel_ev
            try:
                packet = RNS.Packet(link, chat_msg.to_json().encode("utf-8"))
                packet.send()

                resource_holder = {"resource": None}

                def wrapped_progress(resource):
                    if cancel_ev.is_set() or transfer_id in self._cancelled_transfers:
                        try:
                            if hasattr(resource, "cancel"):
                                resource.cancel()
                            elif hasattr(resource, "close"):
                                resource.close()
                        except Exception:
                            pass
                        return
                    if progress_callback:
                        progress_callback(resource)
                    try:
                        pct = int(resource.get_progress() * 100)
                        self._emit_progress(fname, pct, fsize, direction="send", transfer_id=transfer_id)
                    except Exception:
                        pass

                f = open(file_path, "rb")
                self._file_handles[transfer_id] = f
                resource = RNS.Resource(f, link,
                             callback=self._resource_send_callback(fname, transfer_id, fsize),
                             progress_callback=wrapped_progress,
                             auto_compress=False)
                resource_holder["resource"] = resource
                self._active_resources[transfer_id] = resource
                print(f"[messaging] Sent file: {fname} ({fsize} bytes)")
                self._sent_messages[chat_msg.msg_id] = chat_msg
                return chat_msg
            except Exception as e:
                print(f"[messaging] File send failed: {e}")
                self._emit_progress(fname, 0, fsize, status="failed", direction="send", transfer_id=transfer_id)
                self._cleanup_transfer(transfer_id)
                return False

    def _resource_send_callback(self, fname, transfer_id=None, fsize=0):
        def callback(resource):
            self._cleanup_transfer(transfer_id)
            if transfer_id in self._cancelled_transfers:
                self._cancelled_transfers.discard(transfer_id)
                print(f"[messaging] File transfer cancelled: {fname}")
                self._emit_progress(fname, 0, fsize, status="cancelled", direction="send", transfer_id=transfer_id)
                if self._current_transfer_id == transfer_id:
                    self._current_transfer_id = None
                return
            print(f"[messaging] File transfer complete: {fname}")
            status = "complete"
            try:
                if resource.status != RNS.Resource.COMPLETE:
                    status = "failed"
            except Exception:
                pass
            pct = 100 if status == "complete" else 0
            self._emit_progress(fname, pct, fsize, status=status, direction="send", transfer_id=transfer_id)
            if self._current_transfer_id == transfer_id:
                self._current_transfer_id = None
        return callback
