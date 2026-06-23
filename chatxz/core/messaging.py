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
    lan_mesh_has_peer,
    online_interfaces,
    peer_path_entry,
    peer_path_on_family,
    request_path_for_hash,
    request_paths_for_hash,
    scrub_peer_path,
    serial_interface_online,
    register_udp_peer_ip,
    unicast_announce_packet,
    wait_for_peer_path,
)
from chatxz.utils.platform import is_android
from chatxz.core.rns_interfaces import prune_dead_serial_interfaces

APP_NAME = "chatxz"
LINK_CONNECT_TIMEOUT_S = 12
ANDROID_LINK_CONNECT_TIMEOUT_S = 14
FAILOVER_CONNECT_TIMEOUT_S = 16
LINK_CONNECT_POLL_S = 0.05
IDENTITY_WAIT_TIMEOUT_S = 12
ANDROID_IDENTITY_WAIT_TIMEOUT_S = 16
REVERSE_CONNECT_WAIT_S = 10
ANDROID_REVERSE_CONNECT_WAIT_S = 12
INITIATOR_INBOUND_WAIT_S = 8
ANDROID_INITIATOR_INBOUND_WAIT_S = 10
QUICK_OUTBOUND_TIMEOUT_S = 6
HTTP_WAKE_TIMEOUT_S = 1.5
LINK_FAILOVER_GRACE_S = 30
LINK_STALE_FAILOVER_IDLE_S = 90
SESSION_RECONNECT_MIN_IDLE_S = 18
RECEIPT_FAILOVER_TIMEOUT_S = 30
RECEIPT_FAILOVER_MIN_PENDING = 2
MAX_CONCURRENT_RECEIVES = 2
QUEUE_RETRY_INTERVAL_S = 5
_NO_COMPRESS_SUFFIXES = frozenset({
    ".apk", ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".mp4", ".mkv", ".webm", ".mov", ".m4v",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic",
    ".mp3", ".ogg", ".opus", ".wav", ".flac", ".aac",
    ".pdf", ".deb", ".rpm", ".jar", ".aar",
})

MESSAGE_TYPE_TEXT = "text"
MESSAGE_TYPE_FILE = "file"
MESSAGE_TYPE_IMAGE = "image"
MESSAGE_TYPE_VOICE = "voice"
MESSAGE_TYPE_VIDEO = "video"
MESSAGE_TYPE_EMOJI = "emoji"
MESSAGE_TYPE_LONGTEXT = "longtext"
HUB_GROUP_PEER = "__hub_group__"

class ChatMessage:
    def __init__(self, msg_type, content, sender=None, timestamp=None, file_name=None, file_size=None, msg_id=None):
        self.msg_type = msg_type
        self.content = content
        self.sender = sender
        self.timestamp = timestamp or time.time()
        self.file_name = file_name
        self.file_size = file_size
        self.msg_id = msg_id or str(uuid.uuid4())[:12]
        self.hub_group = False

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
        if self.hub_group:
            d["hub"] = True
        return d

    @classmethod
    def from_dict(cls, d):
        msg = cls(
            msg_type=d.get("type", MESSAGE_TYPE_TEXT),
            content=d.get("content", ""),
            sender=d.get("sender"),
            timestamp=d.get("timestamp", time.time()),
            file_name=d.get("file_name"),
            file_size=d.get("file_size"),
            msg_id=d.get("msg_id"),
        )
        msg.hub_group = bool(d.get("hub"))
        return msg

    def to_json(self):
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, data):
        return cls.from_dict(json.loads(data))

class MessagingBackend:
    def __init__(self, identity, config_dir, on_message=None, on_file=None,
                 on_progress=None, on_link_established=None, on_link_closed=None,
                 display_name="", auto_announce=False,
                 receive_dir=None, peer_resolver=None, on_queue_sent=None):
        self.identity = identity
        self.config_dir = config_dir
        self.receive_dir = receive_dir or os.path.join(config_dir, "received")
        self.on_message = on_message
        self.on_file = on_file
        self.on_progress = on_progress
        self.on_link_established = on_link_established
        self.on_link_closed = on_link_closed
        self.on_queue_sent = on_queue_sent
        self.display_name = display_name
        self.auto_announce = auto_announce
        self.announce_interval = 45 if is_android() else 30
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
        self._transfer_bytes_state = {}
        self.my_dest_hash = None
        self.identity_to_dest = {}
        self.dest_to_identity = {}
        self._send_link = None
        self.peer_resolver = peer_resolver
        self._link_peer_hashes = {}
        self._link_handoff = False
        self._last_handoff = False
        self._failover_last_attempt = 0
        self._failover_cooldown_s = 20
        self._failover_in_progress = False
        self._last_link_established_at = 0
        self._last_link_lost_at = 0
        self._session_peer_hash = None
        self._pending_sends = {}
        self._longtext_temp_paths = {}
        self._queue_retry_thread = None
        self.peer_links = {}
        self._connect_user_initiated = False
        self._connect_background = False

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

    def _link_for_peer(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        link = self.peer_links.get(peer)
        if link:
            return link
        for cached_peer, cached_link in self.peer_links.items():
            if self.hashes_equivalent(cached_peer, peer):
                return cached_link
        for link_id, cached in self._link_peer_hashes.items():
            if self.hashes_equivalent(cached, peer):
                return self.links.get(link_id)
        if self.active_peer_hash and self.hashes_equivalent(peer, self.active_peer_hash):
            return self.active_link
        return None

    def _register_peer_link(self, link, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown" or not link:
            return
        self.peer_links[peer] = link
        self._cache_link_peer(link, peer)

    def _unlink_peer(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer:
            return
        self.peer_links.pop(peer, None)
        for key in list(self.peer_links.keys()):
            if self.hashes_equivalent(key, peer):
                self.peer_links.pop(key, None)

    def linked_peers(self):
        out = []
        for peer, link in list(self.peer_links.items()):
            try:
                if getattr(link, "status", None) == RNS.Link.CLOSED:
                    continue
            except Exception:
                pass
            out.append(peer)
        return out

    def disconnect_peer(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        link = self._link_for_peer(peer)
        if not link:
            return False
        try:
            link.teardown()
        except Exception:
            pass
        return True

    def disconnect_all_peers(self, clear_session=True):
        """Tear down every open RNS link (network reset / full disconnect)."""
        self._link_handoff = True
        try:
            seen = set()
            for link in list(self.links.values()):
                lid = getattr(link, "link_id", None)
                if lid and lid in seen:
                    continue
                if lid:
                    seen.add(lid)
                try:
                    link.teardown()
                except Exception:
                    pass
            self.peer_links.clear()
            self._link_peer_hashes.clear()
            self.active_link = None
            self.active_peer_hash = None
            self._send_link = None
            if clear_session:
                self.clear_session_peer()
        finally:
            self._link_handoff = False

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

    def _has_active_transfer(self):
        """True while a file send or receive is in progress on any link."""
        if self._current_transfer_id or self._active_resources:
            return True
        with self._pending_lock:
            for queue in self._pending_files.values():
                if queue:
                    return True
        for link in self.links.values():
            incoming = getattr(link, "incoming_resources", None) or []
            if incoming:
                return True
        return False

    def _migrate_pending_files(self, old_link_id, new_link_id):
        if not old_link_id or old_link_id == new_link_id:
            return
        with self._pending_lock:
            queue = self._pending_files.pop(old_link_id, [])
            if queue:
                self._pending_files.setdefault(new_link_id, []).extend(queue)
                print(f"[transfer] Migrated {len(queue)} pending receive(s) to new link")

    def _flush_pending_files_failed(self, link_id):
        with self._pending_lock:
            queue = self._pending_files.pop(link_id, [])
        for chat_msg in queue:
            print(f"[transfer] Dropped pending receive: {chat_msg.file_name}")
            self._emit_progress(
                chat_msg.file_name or "file",
                0,
                total_size=chat_msg.file_size or 0,
                direction="receive",
                transfer_id=chat_msg.msg_id,
                status="failed",
            )

    def _interface_healthy(self, iface):
        return interface_is_healthy(iface)

    def _interface_path_score(self, iface):
        if not self._interface_healthy(iface):
            return 0
        fam = interface_family(iface)
        if fam == "lan":
            return 100
        if fam == "serial":
            return 60 if not self._lan_transport_ready() else 25
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

    def _peer_has_path_on_family(self, dest_hash, family):
        clean = normalize_hash(dest_hash)
        if len(clean) != 32:
            return False
        return peer_path_on_family(clean, family) is not None

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
        if family == "serial":
            return serial_interface_online() is not None
        if family == "lan":
            return lan_mesh_has_peer() or bool(online_interfaces(family="udp"))
        return bool(online_interfaces(family=family))

    def _preferred_failover_family(self, peer, attached=None):
        attached = attached or self._link_attached_interface(self.active_link)
        att_fam = interface_family(attached)
        if att_fam == "serial" and self._has_online_family("lan"):
            return "lan"
        if att_fam == "lan" and not lan_mesh_has_peer():
            if bool(online_interfaces(family="udp")):
                return "udp"
            if self._has_online_family("serial"):
                return "serial"
        if att_fam == "lan" and self._has_online_family("serial"):
            return "serial"
        if att_fam == "udp" and self._has_online_family("serial"):
            return "serial"
        if att_fam == "udp" and lan_mesh_has_peer():
            return "lan"
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

    def _prepare_failover_path(self, peer, prefer_family=None, peer_ip=None):
        prune_dead_serial_interfaces()
        _, path_iface = peer_path_entry(peer)
        if path_iface and not interface_is_healthy(path_iface):
            clear_peer_path(peer)
        else:
            scrub_peer_path(peer)
        detached = detach_unhealthy_interfaces()
        if detached:
            print(f"[connect] Detached {detached} offline RNS interface(s)")
        self._silent_announce(peer_ip=peer_ip)
        request_paths_for_hash(peer, family=prefer_family)
        if prefer_family:
            wait_s = 12.0 if prefer_family in ("lan", "udp") else 18.0
            path_iface = wait_for_peer_path(peer, family=prefer_family, timeout_s=wait_s)
            if path_iface:
                print(f"[connect] Path ready on {type(path_iface).__name__} ({prefer_family})")
                return True
        path_iface = wait_for_peer_path(peer, family=None, timeout_s=12.0)
        if path_iface:
            print(f"[connect] Path ready on {type(path_iface).__name__}")
            return True
        print(f"[connect] Waiting for path to {peer[:16]}... (no {prefer_family or 'usable'} path yet)")
        return False

    def link_needs_failover(self):
        if not self.active_link or not self.active_peer_hash:
            return False, ""
        if self._has_active_transfer():
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
                # UDP-LAN: ignore path-table flaps while the current link is healthy.
                if path_fam == att_fam == "udp" and self._link_interface_healthy(self.active_link):
                    pass
                elif path_fam != att_fam:
                    if not in_grace and new_score > old_score + 10:
                        return True, f"path moved to {path_fam} (link on {att_fam})"
                elif not in_grace and new_score > old_score + 25:
                    return True, f"better path on {type(path_iface).__name__}"

        if att_fam == "lan" and not lan_mesh_has_peer():
            if bool(online_interfaces(family="udp")):
                return True, "AutoInterface down, UDP available"
            if self._has_online_family("serial"):
                return True, "LAN down, serial available"

        if att_fam == "udp" and not bool(online_interfaces(family="udp")):
            if lan_mesh_has_peer():
                return True, "UDP down, AutoInterface available"
            if self._has_online_family("serial"):
                return True, "UDP down, serial available"

        if att_fam == "serial" and not self._has_online_family("serial") and self._has_online_family("lan"):
            return True, "serial down, LAN available"

        if len(self._pending_sends) >= RECEIPT_FAILOVER_MIN_PENDING:
            oldest = min(self._pending_sends.values())
            if (time.time() - oldest) > RECEIPT_FAILOVER_TIMEOUT_S:
                try:
                    if getattr(self.active_link, "status", None) == RNS.Link.STALE:
                        return True, "send receipt timeout (link stale)"
                except Exception:
                    pass
                if (time.time() - self._last_link_established_at) > LINK_FAILOVER_GRACE_S:
                    return True, "send receipt timeout (link may be dead)"

        if not self._peer_has_path(peer) and not in_grace:
            alt = self._preferred_failover_family(peer, attached)
            if alt and self._has_online_family(alt):
                return True, f"path lost, trying {alt}"
            if not self._link_interface_healthy(self.active_link):
                return True, "no path to peer (link interface dead)"

        try:
            if getattr(self.active_link, "status", None) == RNS.Link.STALE:
                inactive = self.active_link.inactive_for()
                if inactive > LINK_STALE_FAILOVER_IDLE_S:
                    return True, f"link stale ({inactive:.0f}s idle)"
        except Exception:
            pass

        return False, ""

    def session_needs_reconnect(self):
        """True when the primary session peer's RNS link is missing or unhealthy."""
        peer = self.dest_hash_for(self.active_peer_hash or self._session_peer_hash or "")
        if not peer or peer == "unknown":
            return False, ""
        if self._peer_link_active(peer):
            if self.active_link and self._link_interface_healthy(self.active_link):
                return self.link_needs_failover()
            return False, ""
        if self._failover_in_progress:
            return False, ""
        if self.active_link:
            return self.link_needs_failover()
        if self._has_active_transfer():
            return False, ""
        if self._last_link_lost_at and (time.time() - self._last_link_lost_at) < SESSION_RECONNECT_MIN_IDLE_S:
            return False, ""
        if time.time() - self._failover_last_attempt < self._failover_cooldown_s:
            return False, ""
        return True, "link dropped â€” reconnecting"

    def clear_session_peer(self):
        self._session_peer_hash = None
        self._pending_sends.clear()

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
        old_id = old.link_id if old else None
        old_score = self._link_path_score(old)
        new_score = self._link_path_score(link)
        self._link_handoff = True
        self._last_handoff = new_score > old_score + 8
        try:
            print(
                f"[messaging] Path switch to {peer_hash[:16]} "
                f"(score {self._link_path_score(link)} vs {self._link_path_score(old)})"
            )
            self._setup_link(link)
            self._cache_link_peer(link, peer_hash)
            self._notify_link_established(link, peer_hash)
            self._send_link = link
            self._migrate_pending_files(old_id, link.link_id)
            if old and old.link_id != link.link_id:
                try:
                    old.teardown()
                except Exception:
                    pass
            self.drain_queue(link, peer_hash, include_files=False)
        finally:
            self._link_handoff = False

    def _outgoing_link(self, peer_hash=None):
        if peer_hash:
            link = self._link_for_peer(peer_hash)
            if link:
                return link
        if self.active_peer_hash:
            link = self._link_for_peer(self.active_peer_hash)
            if link:
                return link
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

    def enqueue(self, msg_type, content, target_hash=None, file_name=None, file_size=None, file_path=None, msg_id=None):
        msg_id = msg_id or str(uuid.uuid4())[:12]
        for entry in self.message_queue:
            if entry.get("msg_id") == msg_id:
                print(f"[queue] Already queued {msg_type} ({msg_id[:8]})")
                return
        entry = {
            "type": msg_type,
            "content": content,
            "target_hash": target_hash,
            "file_name": file_name,
            "file_size": file_size,
            "file_path": file_path,
            "msg_id": msg_id,
            "timestamp": time.time(),
        }
        self.message_queue.append(entry)
        self._save_queue()
        print(f"[queue] Enqueued {msg_type} for target {target_hash[:16] if target_hash else 'any (next peer)'}")

    def _queue_matches_target(self, entry, target_hash):
        tgt = entry.get("target_hash")
        if not tgt:
            return not target_hash
        if not target_hash:
            return False
        return self.hashes_equivalent(tgt, target_hash)

    def drain_queue(self, link, target_hash, include_files=True):
        if not link or not target_hash:
            return 0
        remaining = []
        sent = 0
        for entry in self.message_queue:
            if not self._queue_matches_target(entry, target_hash):
                remaining.append(entry)
                continue
            try:
                if entry["type"] in ("text", "emoji"):
                    result = self.send_message(
                        entry["content"],
                        msg_id=entry.get("msg_id"),
                        target_peer=target_hash,
                    )
                    if result:
                        sent += 1
                        if self.on_queue_sent:
                            try:
                                self.on_queue_sent(result, target_hash, entry)
                            except Exception as e:
                                print(f"[queue] on_queue_sent error: {e}")
                    else:
                        remaining.append(entry)
                elif entry["type"] in ("file", "image", "video", "voice"):
                    if not include_files:
                        remaining.append(entry)
                        continue
                    fp = entry.get("file_path") or entry.get("content")
                    if fp and os.path.exists(fp):
                        result = self.send_file(
                            fp,
                            entry["type"],
                            transfer_id=entry.get("msg_id"),
                            target_peer=target_hash,
                        )
                        if result:
                            sent += 1
                            if self.on_queue_sent:
                                try:
                                    self.on_queue_sent(result, target_hash, entry)
                                except Exception as e:
                                    print(f"[queue] on_queue_sent error: {e}")
                        else:
                            remaining.append(entry)
                    else:
                        print(f"[queue] File no longer exists: {fp}")
            except Exception as e:
                print(f"[queue] Failed to send: {e}")
                remaining.append(entry)
        if sent:
            print(f"[queue] Drained {sent} queued items for {target_hash[:16] if target_hash else 'peer'}...")
        self.message_queue = remaining
        self._save_queue()
        return sent

    def clear_queue(self, target_hash=None):
        if not target_hash:
            self.message_queue = []
        else:
            self.message_queue = [
                e for e in self.message_queue
                if not self._queue_matches_target(e, target_hash)
            ]
        self._save_queue()

    def retry_queue(self):
        if not self.message_queue:
            return 0
        targets = set()
        for entry in self.message_queue:
            tgt = entry.get("target_hash")
            if tgt:
                targets.add(self.dest_hash_for(tgt))
        if not targets:
            if self.active_peer_hash:
                targets.add(self.dest_hash_for(self.active_peer_hash))
        sent = 0
        for peer in targets:
            link = self._link_for_peer(peer)
            if not link:
                continue
            try:
                if getattr(link, "status", None) != RNS.Link.ACTIVE:
                    continue
            except Exception:
                pass
            sent += self.drain_queue(link, peer, include_files=True)
        return sent

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
        self._queue_retry_thread = threading.Thread(target=self._queue_retry_loop, name="chatxz-queue-retry", daemon=True)
        self._queue_retry_thread.start()
        print(f"[messaging] Started (auto_announce={self.auto_announce})")
        return self.destination

    def _queue_retry_loop(self):
        while self.running:
            for _ in range(QUEUE_RETRY_INTERVAL_S):
                if not self.running:
                    return
                time.sleep(1)
            if self.message_queue and self.peer_links:
                try:
                    self.retry_queue()
                except Exception as e:
                    print(f"[queue] Retry loop error: {e}")

    def announce(self):
        self._announce()

    def _silent_announce(self, peer_ip=None):
        """RNS path refresh only â€” no subnet beacon probe."""
        if not self.destination:
            return
        prune_dead_serial_interfaces()
        announce_data = json.dumps({
            "app": APP_NAME,
            "name": self.display_name or ""
        }).encode("utf-8")
        self.destination.announce(app_data=announce_data)
        if peer_ip:
            packet = build_announce_packet(self.destination, announce_data)
            unicast_announce_packet(packet, peer_ip=peer_ip, subnet_probe=False)

    def _announce(self, peer_ip=None, unicast_subnet=None):
        if not self.destination:
            return
        prune_dead_serial_interfaces()
        announce_data = json.dumps({
            "app": APP_NAME,
            "name": self.display_name or ""
        }).encode("utf-8")
        self.destination.announce(app_data=announce_data)
        if unicast_subnet is None:
            unicast_subnet = True
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

    def _lan_transport_ready(self):
        return lan_mesh_has_peer() or bool(online_interfaces(family="udp"))

    def _serial_transport_ready(self):
        return serial_interface_online() is not None

    def _http_peer_post(self, peer_ip, peer_port, path, payload=None, timeout=HTTP_WAKE_TIMEOUT_S):
        if not peer_ip:
            return False
        port = int(peer_port or 8742)
        url = f"http://{peer_ip}:{port}{path}"
        try:
            data = None
            headers = {}
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"
            req = urlrequest.Request(url, data=data, headers=headers, method="POST")
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except Exception as exc:
            print(f"[connect] HTTP {path} to {peer_ip} failed: {exc}")
            return False

    def _request_peer_announce(self, peer_ip, peer_port):
        """Ask peer to refresh RNS path only (no discovery/beacon broadcast)."""
        return self._http_peer_post(peer_ip, peer_port, "/api/path_wake", payload={})

    def _request_peer_connect(self, peer_ip, peer_port, my_hash, caller_ip=None, caller_port=8742):
        """Ask peer to open outbound RNS link back to us (we wait inbound)."""
        payload = {
            "hash": normalize_hash(my_hash or self.my_dest_hash or ""),
            "ip": caller_ip or "",
            "port": int(caller_port or 8742),
            "outbound": True,
        }
        return self._http_peer_post(peer_ip, peer_port, "/api/request_connect", payload=payload)

    def _wake_peer(self, peer_ip, peer_port, my_hash, caller_ip=None, caller_port=8742):
        """Wake peer for reverse RNS connect and refresh its LAN announces."""
        if not peer_ip:
            return False
        register_udp_peer_ip(peer_ip)
        results = {"connect": False, "announce": False}

        def _connect():
            results["connect"] = self._request_peer_connect(
                peer_ip, peer_port, my_hash,
                caller_ip=caller_ip, caller_port=caller_port,
            )

        def _announce():
            results["announce"] = self._request_peer_announce(peer_ip, peer_port)

        t_connect = threading.Thread(target=_connect, daemon=True)
        t_announce = threading.Thread(target=_announce, daemon=True)
        t_connect.start()
        t_announce.start()
        t_connect.join(timeout=HTTP_WAKE_TIMEOUT_S + 0.5)
        t_announce.join(timeout=HTTP_WAKE_TIMEOUT_S + 0.5)
        return results["connect"] or results["announce"]

    def _prime_udp_path(self, dest_hex, peer_ip=None, timeout_s=None):
        """Establish a UDP RNS path before opening a link (required for Android peers)."""
        if timeout_s is None:
            timeout_s = 6.0 if is_android() else 4.0
        self._silent_announce(peer_ip=peer_ip)
        request_paths_for_hash(dest_hex, family="udp")
        path_iface = wait_for_peer_path(dest_hex, family="udp", timeout_s=timeout_s)
        if path_iface:
            print(f"[connect] UDP path ready via {type(path_iface).__name__}")
            return True
        return False

    def _prime_serial_path(self, dest_hex, timeout_s=18.0):
        """Establish an RNS path over USB serial (no LAN/HTTP wake required)."""
        if not self._serial_transport_ready():
            return False
        print("[connect] Priming serial RNS path...")
        self._silent_announce()
        request_paths_for_hash(dest_hex, family="serial")
        path_iface = wait_for_peer_path(dest_hex, family="serial", timeout_s=timeout_s)
        if path_iface:
            print(f"[connect] Serial path ready via {type(path_iface).__name__}")
            return True
        print("[connect] Serial path not ready yet â€” ensure both ends have USB serial configured")
        return False

    def _establish_outbound_link(self, destination, dest_hex, clean, old_link=None,
                                 timeout_s=LINK_CONNECT_TIMEOUT_S, promote_active=None):
        """Try to open an outbound RNS link within timeout_s."""
        link = None
        try:
            link = RNS.Link(destination)
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                if self._interrupted():
                    self._teardown_outbound_attempt(link)
                    return False
                time.sleep(LINK_CONNECT_POLL_S)
                if self._peer_link_active(dest_hex, clean):
                    self._teardown_outbound_attempt(link)
                    return True
                try:
                    if link.status == RNS.Link.ACTIVE:
                        if old_link and old_link.link_id != link.link_id:
                            old_peer = self._link_peer_hashes.get(old_link.link_id)
                            if old_peer and self.hashes_equivalent(old_peer, dest_hex):
                                self._link_handoff = True
                                try:
                                    old_link.teardown()
                                except Exception:
                                    pass
                                finally:
                                    self._link_handoff = False
                                self._last_handoff = True
                            else:
                                self._last_handoff = False
                        else:
                            self._last_handoff = False
                        self._setup_link(link)
                        if promote_active is None:
                            promote_active = (
                                self._connect_user_initiated and not self._connect_background
                            )
                        background = not promote_active
                        self._notify_link_established(
                            link, dest_hex,
                            promote_active=promote_active,
                            background=background,
                        )
                        if promote_active:
                            self._send_link = link
                        try:
                            link.identify(self.identity)
                        except Exception:
                            pass
                        print("[connect] Link established")
                        self.drain_queue(link, dest_hex, include_files=not self._failover_in_progress)
                        return True
                    if link.status == RNS.Link.CLOSED:
                        break
                except Exception:
                    pass
                if self.active_link and link and self.active_link.link_id == link.link_id:
                    return True
        except Exception as e:
            print(f"[connect] Link failed: {e}")
        finally:
            self._teardown_outbound_attempt(link)
        return self._peer_link_active(dest_hex, clean)

    def _peer_link_active(self, dest_hex, alt_hex=None):
        for peer in (dest_hex, alt_hex):
            if not peer:
                continue
            link = self._link_for_peer(peer)
            if not link:
                continue
            try:
                if link.status == RNS.Link.ACTIVE:
                    return True
            except Exception:
                return True
        return False

    def _wait_for_peer_link(self, dest_hex, alt_hex=None, timeout_s=REVERSE_CONNECT_WAIT_S):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._interrupted():
                return False
            if self._peer_link_active(dest_hex, alt_hex):
                return True
            time.sleep(LINK_CONNECT_POLL_S)
        return False

    def _wait_for_reverse_link(self, dest_hex, alt_hex=None, timeout_s=REVERSE_CONNECT_WAIT_S):
        return self._wait_for_peer_link(dest_hex, alt_hex=alt_hex, timeout_s=timeout_s)

    def _teardown_outbound_attempt(self, link):
        if not link:
            return
        try:
            if link.status != RNS.Link.ACTIVE:
                link.teardown()
        except Exception:
            pass
        if link.link_id in self.links:
            del self.links[link.link_id]

    def _announce_loop(self):
        while self.running:
            for _ in range(self.announce_interval):
                if not self.running:
                    return
                time.sleep(1)
            if self._has_active_transfer():
                continue
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
        wait_s = ANDROID_IDENTITY_WAIT_TIMEOUT_S if is_android() else IDENTITY_WAIT_TIMEOUT_S
        deadline = time.time() + wait_s
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

    def _notify_link_established(self, link, peer_hash=None, promote_active=True, background=False):
        peer = self.dest_hash_for(peer_hash or self._peer_destination_hash(link))
        if not peer or peer == "unknown":
            peer = self.dest_hash_for(self.active_peer_hash or "")
        if not peer or peer == "unknown":
            return
        self._register_peer_link(link, peer)
        self._last_link_established_at = time.time()
        if promote_active:
            old_active = self.active_peer_hash
            self.active_link = link
            self.active_peer_hash = peer
            self._session_peer_hash = peer
            self._send_link = link
            if not old_active or self.hashes_equivalent(peer, old_active):
                self._pending_sends.clear()
        label = "background" if background else "active"
        print(f"[messaging] Link ready with {peer[:16]}... ({label})")
        if self.on_link_established:
            try:
                self.on_link_established(peer, link, background=background, promote_active=promote_active)
            except TypeError:
                try:
                    self.on_link_established(peer, link)
                except Exception as e:
                    print(f"[messaging] on_link_established error: {e}")
            except Exception as e:
                print(f"[messaging] on_link_established error: {e}")

    def _active_incoming_resources(self, link):
        try:
            incoming = getattr(link, "incoming_resources", None) or []
        except Exception:
            return []
        active = []
        for res in incoming:
            try:
                status = getattr(res, "status", None)
                if status in (RNS.Resource.COMPLETE, RNS.Resource.FAILED):
                    continue
            except Exception:
                pass
            active.append(res)
        return active

    def _resource_accept_callback(self, link):
        def callback(resource_ad):
            active = self._active_incoming_resources(link)
            if len(active) >= MAX_CONCURRENT_RECEIVES:
                print(f"[transfer] Deferring resource ({len(active)} receive(s) active)")
                return False
            return True
        return callback

    def _setup_link(self, link):
        self.links[link.link_id] = link
        link.set_link_closed_callback(self._link_closed(link))
        link.set_packet_callback(self._packet_callback(link))
        try:
            link.set_resource_strategy(RNS.Link.ACCEPT_APP)
            link.set_resource_callback(self._resource_accept_callback(link))
            link.set_resource_concluded_callback(self._resource_concluded(link))
            print(f"[messaging] Resource strategy ACCEPT_APP for link {link.link_id.hex()[:12]}")
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
                if self._has_active_transfer():
                    print(f"[messaging] Keeping current link during active transfer ({peer_hash[:16]}...)")
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

        existing = self._link_for_peer(peer_hash)
        if existing and existing.link_id != link.link_id:
            print(
                f"[messaging] Replacing stale link from {peer_hash[:16]}... "
                f"({existing.link_id.hex()[:12]} -> {link.link_id.hex()[:12]})"
            )
            try:
                existing.teardown()
            except Exception:
                pass

        print(f"[messaging] Incoming link established: {link.link_id.hex()[:12]} ({peer_hash[:16]}...)")
        self._last_handoff = False
        self._setup_link(link)
        promote = not self.active_link or self.hashes_equivalent(peer_hash, self.active_peer_hash)
        self._notify_link_established(
            link, peer_hash,
            promote_active=promote,
            background=not promote,
        )
        self.drain_queue(link, peer_hash)

    def _link_closed(self, link):
        def callback(link):
            remote_hash = self.dest_hash_for(self._peer_for_link(link))
            if link.link_id in self.links:
                del self.links[link.link_id]
            self._link_peer_hashes.pop(link.link_id, None)
            if remote_hash and remote_hash != "unknown":
                self._unlink_peer(remote_hash)
            if not self._link_handoff:
                self._flush_pending_files_failed(link.link_id)
            closing_active = self.active_link and self.active_link.link_id == link.link_id
            if closing_active and not self._link_handoff:
                if self.active_peer_hash:
                    self._session_peer_hash = self.active_peer_hash
                self.active_link = None
                self.active_peer_hash = None
                self._last_link_lost_at = time.time()
                remaining = self.linked_peers()
                if remaining:
                    next_peer = remaining[0]
                    next_link = self._link_for_peer(next_peer)
                    if next_link:
                        self.active_link = next_link
                        self.active_peer_hash = next_peer
                        self._send_link = next_link
            if self._send_link and self._send_link.link_id == link.link_id:
                self._send_link = self.active_link
            if self.on_link_closed and not self._link_handoff:
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
                        self._pending_sends.pop(msg_id, None)
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
                    self._start_receive_progress_watch(link, chat_msg)
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

                    from chatxz.utils.helpers import safe_basename, safe_path_under
                    os.makedirs(self.receive_dir, exist_ok=True)
                    raw_name = chat_msg.file_name or f"file_{int(time.time())}"
                    fname = safe_basename(raw_name, default=f"file_{int(time.time())}")
                    save_path = safe_path_under(self.receive_dir, fname)
                    if not save_path:
                        print(f"[messaging] Rejected unsafe filename: {raw_name!r}")
                        return

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

    def _calc_transfer_speed(self, transfer_id, bytes_done):
        key = transfer_id or "default"
        now = time.time()
        state = self._transfer_bytes_state.get(key, {})
        last_bytes = state.get("bytes", 0)
        last_ts = state.get("ts", now)
        elapsed = max(now - last_ts, 0.001)
        speed_bps = max(0, int((bytes_done - last_bytes) / elapsed))
        if bytes_done > last_bytes or (now - last_ts) > 1.0:
            self._transfer_bytes_state[key] = {"bytes": bytes_done, "ts": now, "speed": speed_bps}
        return format_speed(self._transfer_bytes_state.get(key, {}).get("speed", speed_bps))

    def _start_receive_progress_watch(self, link, chat_msg):
        def watch():
            deadline = time.time() + 7200
            fname = chat_msg.file_name or "file"
            tid = chat_msg.msg_id
            fsize = chat_msg.file_size or 0
            while time.time() < deadline:
                if link.link_id not in self.links:
                    return
                try:
                    incoming = getattr(link, "incoming_resources", None) or []
                    if not incoming:
                        time.sleep(0.35)
                        continue
                    for res in incoming:
                        pct = int(float(res.get_progress()) * 100)
                        transferred = int(float(res.get_progress()) * fsize) if fsize else 0
                        speed = self._calc_transfer_speed(tid, transferred)
                        self._emit_progress(
                            fname, pct, fsize, speed=speed,
                            direction="receive", transfer_id=tid, status="active",
                        )
                        if getattr(res, "status", None) == RNS.Resource.COMPLETE:
                            return
                except Exception:
                    pass
                time.sleep(0.35)

        threading.Thread(target=watch, name=f"recv-progress-{chat_msg.msg_id[:8]}", daemon=True).start()

    def _emit_progress(self, file_name, progress, total_size=0, speed="", direction="receive", transfer_id=None, status="active"):
        if transfer_id and transfer_id in self._cancelled_transfers and status == "active":
            return
        if status in ("complete", "cancelled", "failed"):
            self._progress_last.pop(transfer_id or file_name, None)
            self._transfer_bytes_state.pop(transfer_id or file_name, None)
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

    def _teardown_active_link(self, preserve_peer=False, handoff=False, clear_session=False):
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
            if clear_session:
                self.clear_session_peer()
        finally:
            if handoff:
                self._link_handoff = False

    def resume_session_peer(self, peer_ip=None, peer_port=None, peer_lookup=None,
                            caller_ip=None, caller_port=8742):
        """Reconnect to the saved session peer after link drop or UI resume."""
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        if not peer or peer == "unknown":
            return False
        if self.active_link and self._peer_link_active(peer):
            return True
        if self._failover_in_progress:
            return False
        print(f"[connect] Resuming session with {peer[:16]}...")
        return self.reconnect_active_peer(
            peer_ip, peer_port, peer_lookup, caller_ip, caller_port,
            reason="session resume",
        )

    def reconnect_active_peer(self, peer_ip=None, peer_port=None, peer_lookup=None,
                              caller_ip=None, caller_port=8742, reason=""):
        now = time.time()
        if self._failover_in_progress:
            return False
        if now - self._failover_last_attempt < self._failover_cooldown_s:
            return False
        peer = self.dest_hash_for(self.active_peer_hash or self._session_peer_hash or "")
        if not peer or peer == "unknown":
            return False
        if not self.active_peer_hash:
            self.active_peer_hash = peer

        self._failover_last_attempt = now
        self._failover_in_progress = True
        try:
            prefer = self._preferred_failover_family(peer)
            if prefer == "serial" and not self._has_online_family("serial"):
                prefer = "udp" if self._has_online_family("udp") else (
                    "lan" if lan_mesh_has_peer() else prefer
                )
            print(f"[connect] Failover reconnect to {peer[:16]}... ({reason})")
            self._teardown_active_link(preserve_peer=True, handoff=True)
            time.sleep(0.3)
            if peer_ip:
                register_udp_peer_ip(peer_ip)
            if not self._prepare_failover_path(peer, prefer_family=prefer, peer_ip=peer_ip):
                if prefer == "serial":
                    print("[connect] Serial failover blocked â€” plug in USB serial and ensure port is configured")
                return False
            if peer_ip and self._lan_transport_ready():
                inbound_wait = INITIATOR_INBOUND_WAIT_S
                print(f"[connect] Failover waiting for inbound link ({inbound_wait}s)...")
                if self._wait_for_peer_link(peer, timeout_s=inbound_wait):
                    print("[connect] Failover complete (inbound)")
                    return True
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
                   caller_ip=None, caller_port=8742, replace=False, failover=False,
                   respond_to_wake=False, user_initiated=False):
        with self._connect_lock:
            if self._interrupted():
                return False

            self._connect_user_initiated = bool(user_initiated)
            self._connect_background = bool(respond_to_wake and not user_initiated)

            clean = normalize_hash(destination_hash_hex)
            if len(clean) != 32:
                print(f"[connect] Invalid hash length ({len(clean)} chars, expected 32)")
                return False
            if peer_ip:
                register_udp_peer_ip(peer_ip)

            old_link = None
            if self.active_link and self.active_peer_hash and self.hashes_equivalent(clean, self.active_peer_hash):
                link_ok = self._link_interface_healthy(self.active_link) and self._peer_has_path(clean)
                if not replace:
                    if link_ok:
                        print(f"[connect] Already connected to {self.active_peer_hash[:16]}...")
                        return True
                    print(f"[connect] Stale link to {self.active_peer_hash[:16]}... â€” reconnecting")
                    self._teardown_active_link(preserve_peer=True, handoff=True)
                elif self._link_path_score(self.active_link) >= 90 and link_ok:
                    return True
                else:
                    old_link = self.active_link
                    self._teardown_active_link(preserve_peer=True, handoff=True)
                    print(f"[connect] Replacing link to {self.active_peer_hash[:16]} for better path...")
            elif self._peer_link_active(clean):
                print(f"[connect] Already linked to {clean[:16]}... (parallel session)")
                if user_initiated:
                    link = self._link_for_peer(clean)
                    if link:
                        self._notify_link_established(
                            link, clean, promote_active=True, background=False,
                        )
                return True

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
            if self._peer_link_active(dest_hex, clean):
                print(f"[connect] Already linked to {dest_hex[:16]}... (inbound)")
                return True

            lan_ready = self._lan_transport_ready()
            serial_ready = self._serial_transport_ready()
            serial_only = serial_ready and not lan_ready

            if serial_only:
                print("[connect] Serial-only mode (no LAN) â€” skipping HTTP wake")
                peer_ip = None
                self._prime_serial_path(dest_hex)
                if self._peer_has_path_on_family(dest_hex, "serial"):
                    print(f"[connect] Serial path known â€” quick outbound ({QUICK_OUTBOUND_TIMEOUT_S}s)")
                    if self._establish_outbound_link(
                        destination, dest_hex, clean, old_link=old_link,
                        timeout_s=QUICK_OUTBOUND_TIMEOUT_S,
                    ):
                        return True
            elif serial_ready and self._peer_has_path_on_family(dest_hex, "serial"):
                self._prime_serial_path(dest_hex, timeout_s=8.0)
                print(f"[connect] Serial path available â€” quick outbound ({QUICK_OUTBOUND_TIMEOUT_S}s)")
                if self._establish_outbound_link(
                    destination, dest_hex, clean, old_link=old_link,
                    timeout_s=QUICK_OUTBOUND_TIMEOUT_S,
                ):
                    return True
            elif peer_ip and not respond_to_wake and lan_ready:
                self._prime_udp_path(dest_hex, peer_ip=peer_ip, timeout_s=2.5)
                if self._peer_has_path(dest_hex):
                    print(f"[connect] Path known â€” quick outbound attempt ({QUICK_OUTBOUND_TIMEOUT_S}s)")
                    if self._establish_outbound_link(
                        destination, dest_hex, clean, old_link=old_link,
                        timeout_s=QUICK_OUTBOUND_TIMEOUT_S,
                    ):
                        return True
                print(f"[connect] Waking peer at {peer_ip}:{peer_port or 8742}")
                self._wake_peer(
                    peer_ip, peer_port, my_hash,
                    caller_ip=caller_ip, caller_port=caller_port,
                )
                inbound_wait = (
                    ANDROID_INITIATOR_INBOUND_WAIT_S if is_android()
                    else INITIATOR_INBOUND_WAIT_S
                )
                if self._wait_for_peer_link(dest_hex, alt_hex=clean, timeout_s=1.5):
                    print("[connect] Link established (inbound after wake)")
                    return True
                print(f"[connect] Waiting for peer outbound link ({inbound_wait}s)...")
                if self._wait_for_peer_link(dest_hex, alt_hex=clean, timeout_s=inbound_wait):
                    print("[connect] Link established (inbound after wake)")
                    return True
                print("[connect] Peer did not connect back â€” trying outbound fallback...")
            elif peer_ip and respond_to_wake:
                print(
                    f"[connect] Outbound to caller at {peer_ip}:{peer_port or 8742} "
                    f"({dest_hex[:16]}...)"
                )
            elif serial_ready and not peer_ip:
                self._prime_serial_path(dest_hex, timeout_s=12.0)

            scrub_peer_path(dest_hex)
            if serial_only or (serial_ready and not lan_ready):
                request_paths_for_hash(dest_hex, family="serial")
            elif peer_ip or is_android():
                self._prime_udp_path(dest_hex, peer_ip=peer_ip)
            else:
                request_paths_for_hash(dest_hex)
            if is_android() and not peer_ip and not serial_ready:
                print("[connect] Android: no peer IP â€” connect from Discovered list or add contact with LAN IP")
            connect_timeout = FAILOVER_CONNECT_TIMEOUT_S if failover else (
                ANDROID_LINK_CONNECT_TIMEOUT_S if is_android() else LINK_CONNECT_TIMEOUT_S
            )
            print(f"[connect] Connecting to {dest_hex[:16]}... (timeout {connect_timeout}s)")

            if self._establish_outbound_link(
                destination, dest_hex, clean, old_link=old_link,
                timeout_s=connect_timeout,
            ):
                return True

            if self._peer_link_active(dest_hex, clean):
                print("[connect] Link established (inbound after outbound attempt)")
                return True

            if peer_ip and lan_ready:
                reverse_wait = ANDROID_REVERSE_CONNECT_WAIT_S if is_android() else REVERSE_CONNECT_WAIT_S
                print(f"[connect] Outbound timed out â€” waiting for reverse connect ({reverse_wait}s)...")
                if not respond_to_wake:
                    self._wake_peer(
                        peer_ip, peer_port, my_hash,
                        caller_ip=caller_ip, caller_port=caller_port,
                    )
                if self._wait_for_reverse_link(dest_hex, alt_hex=clean, timeout_s=reverse_wait):
                    print("[connect] Reverse connect established")
                    return True

            print("[connect] Peer not reachable")
            return False

    def send_hub_message(self, text, receipt_callback=None, msg_id=None,
                       hub_server_hash=None, hub_server_mode=False):
        msg = ChatMessage(MESSAGE_TYPE_TEXT, text, msg_id=msg_id)
        msg.hub_group = True
        data = msg.to_json().encode("utf-8")
        if hub_server_mode:
            targets = self.linked_peers()
        elif hub_server_hash:
            targets = [self.dest_hash_for(hub_server_hash)]
        else:
            targets = self.linked_peers()[:1]
        sent = False
        for peer in targets:
            if not peer:
                continue
            link = self._link_for_peer(peer)
            if not link:
                continue
            try:
                packet = RNS.Packet(link, data)
                packet.send()
                sent = True
            except Exception as e:
                print(f"[hub] send failed to {peer[:16]}: {e}")
        if not sent:
            print("[hub] send_hub_message: no active link")
            return False
        print(f"[hub] Sent group message: {text[:50]}...")
        self._sent_messages[msg.msg_id] = msg
        self._pending_sends[msg.msg_id] = time.time()
        if receipt_callback:
            self._receipt_callbacks[msg.msg_id] = receipt_callback
        return msg

    def relay_hub_message(self, chat_msg, sender_hash):
        if not getattr(chat_msg, "hub_group", False):
            return
        data = chat_msg.to_json().encode("utf-8")
        for peer in self.linked_peers():
            if self.hashes_equivalent(peer, sender_hash):
                continue
            link = self._link_for_peer(peer)
            if not link:
                continue
            try:
                RNS.Packet(link, data).send()
            except Exception as e:
                print(f"[hub] relay failed to {peer[:16]}: {e}")

    def send_message(self, text, receipt_callback=None, msg_id=None, target_peer=None):
        peer = self.dest_hash_for(target_peer or self.active_peer_hash or "")
        if not self._peer_link_active(peer):
            print(f"[messaging] send_message: no active link to {peer[:16] if peer else 'peer'}")
            return False
        link = self._outgoing_link(peer)
        if not link:
            print(f"[messaging] send_message: no link to {peer[:16] if peer else 'peer'}")
            return False
        msg = ChatMessage(MESSAGE_TYPE_TEXT, text, msg_id=msg_id)
        data = msg.to_json().encode("utf-8")
        mtu = getattr(link, 'mtu', 500)
        try:
            if len(data) > mtu - 50:
                return self._send_long_text(msg, text, data, receipt_callback, link)
            packet = RNS.Packet(link, data)
            packet.send()
            print(f"[messaging] Sent text message: {text[:50]}...")
            self._sent_messages[msg.msg_id] = msg
            self._pending_sends[msg.msg_id] = time.time()
            if receipt_callback:
                self._receipt_callbacks[msg.msg_id] = receipt_callback
            return msg
        except Exception as e:
            print(f"[messaging] Send failed: {e}")
            return False

    def _send_long_text(self, msg, text, data, receipt_callback, link=None):
        link = link or self._outgoing_link()
        import tempfile as _tf
        tmp = _tf.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
        tmp.write(text)
        tmp_path = tmp.name
        tmp.close()
        fsize = len(data)
        meta = ChatMessage(
            MESSAGE_TYPE_LONGTEXT,
            json.dumps({"msg_id": msg.msg_id, "file_name": "longtext.txt"}),
            msg_id=msg.msg_id,
            file_name="longtext.txt",
            file_size=fsize,
        )
        try:
            packet = RNS.Packet(link, meta.to_json().encode("utf-8"))
            packet.send()
        except Exception as e:
            print(f"[messaging] Long text metadata send failed: {e}")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return False
        try:
            if not self._wait_for_send_slot(timeout_s=120):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return False
            f = open(tmp_path, "rb")
            self._file_handles[msg.msg_id] = f
            self._longtext_temp_paths[msg.msg_id] = tmp_path
            self._current_transfer_id = msg.msg_id

            def longtext_done(resource):
                tmp_cleanup = self._longtext_temp_paths.pop(msg.msg_id, None)
                if tmp_cleanup:
                    try:
                        os.unlink(tmp_cleanup)
                    except Exception:
                        pass
                self._resource_send_callback("longtext.txt", msg.msg_id, fsize)(resource)

            resource = RNS.Resource(
                f, link,
                callback=longtext_done,
                progress_callback=None,
                auto_compress=True,
            )
            self._active_resources[msg.msg_id] = resource
            print(f"[messaging] Sent long text: {text[:50]}... ({fsize} bytes as resource)")
            self._sent_messages[msg.msg_id] = msg
            self._pending_sends[msg.msg_id] = time.time()
            if receipt_callback:
                self._receipt_callbacks[msg.msg_id] = receipt_callback
            return msg
        except Exception as e:
            print(f"[messaging] Long text resource send failed: {e}")
            self._longtext_temp_paths.pop(msg.msg_id, None)
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            self._cleanup_transfer(msg.msg_id)
            return False

    def _wait_for_send_slot(self, timeout_s=180):
        deadline = time.time() + timeout_s
        while self._current_transfer_id or self._active_resources:
            if time.time() > deadline:
                print("[transfer] Timed out waiting for previous transfer to finish")
                return False
            time.sleep(0.15)
        return True

    def send_file(self, file_path, msg_type=MESSAGE_TYPE_FILE, progress_callback=None,
                  transfer_id=None, target_peer=None):
        peer = self.dest_hash_for(target_peer or self.active_peer_hash or "")
        link = self._outgoing_link(peer)
        if not link or not os.path.exists(file_path):
            print(f"[messaging] send_file: no link to {peer[:16] if peer else 'peer'} or missing file")
            return False
        try:
            if getattr(link, "status", None) != RNS.Link.ACTIVE:
                print("[messaging] send_file: link not active")
                return False
        except Exception:
            pass
        with self._file_send_lock:
            if not self._wait_for_send_slot(timeout_s=300):
                return False
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
                        transferred = int(float(resource.get_progress()) * fsize) if fsize else 0
                        speed = self._calc_transfer_speed(transfer_id, transferred)
                        self._emit_progress(
                            fname, pct, fsize, speed=speed,
                            direction="send", transfer_id=transfer_id,
                        )
                    except Exception:
                        pass

                f = open(file_path, "rb")
                self._file_handles[transfer_id] = f
                ext = os.path.splitext(file_path)[1].lower()
                compress = (
                    msg_type not in (MESSAGE_TYPE_IMAGE, MESSAGE_TYPE_VIDEO)
                    and fsize > 65536
                    and ext not in _NO_COMPRESS_SUFFIXES
                )
                resource = RNS.Resource(f, link,
                             callback=self._resource_send_callback(fname, transfer_id, fsize),
                             progress_callback=wrapped_progress,
                             auto_compress=compress)
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
