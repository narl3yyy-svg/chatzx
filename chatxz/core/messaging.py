import threading, RNS, json, time, os, tempfile, uuid, base64, secrets
import urllib.request
import urllib.error
from chatxz.utils.helpers import format_speed
from chatxz.core.discovery import normalize_hash

APP_NAME = "chatxz"
DIRECT_TRANSFER_THRESHOLD = 256 * 1024


class _HttpLink:
    """Placeholder when chat uses HTTP LAN transport instead of RNS."""
    link_id = b"httplanlink01"
    mtu = 65536


_HTTP_LINK = _HttpLink()

MESSAGE_TYPE_TEXT = "text"
MESSAGE_TYPE_FILE = "file"
MESSAGE_TYPE_IMAGE = "image"
MESSAGE_TYPE_VOICE = "voice"
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
                 on_progress=None, on_link_established=None, on_http_session=None,
                 display_name="", auto_announce=False, my_ip=None, my_port=8742,
                 receive_dir=None):
        self.identity = identity
        self.config_dir = config_dir
        self.receive_dir = receive_dir or os.path.join(config_dir, "received")
        self.on_message = on_message
        self.on_file = on_file
        self.on_progress = on_progress
        self.on_link_established = on_link_established
        self.on_http_session = on_http_session
        self.display_name = display_name
        self.auto_announce = auto_announce
        self.announce_interval = 30
        self.my_ip = my_ip
        self.my_port = my_port
        self.my_dest_hash = None
        self.destination = None
        self.lan_transport = "rns"
        self.http_peer = None
        self.links = {}
        self.active_link = None
        self.active_peer_hash = None
        self.running = False
        self._announce_thread = None
        self._pending_files = {}
        self.peer_ips = {}
        self.direct_transfer_tokens = {}
        self.queue_file = os.path.join(config_dir, "queue.json")
        self.message_queue = self._load_queue()
        self._file_send_lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._sent_messages = {}
        self._receipt_callbacks = {}
        self._active_resources = {}
        self._cancel_events = {}
        self._current_transfer_id = None

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
                    elif entry["type"] in ("file", "image", "voice"):
                        fp = entry.get("file_path") or entry.get("content")
                        if fp and os.path.exists(fp):
                            result = self.send_file_smart(fp, entry["type"])
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

    def _announce(self):
        if self.destination:
            announce_data = json.dumps({
                "app": APP_NAME,
                "name": self.display_name or ""
            }).encode("utf-8")
            self.destination.announce(app_data=announce_data)
            print(f"[messaging] Announced on LAN (name={self.display_name or 'none'})")

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

    def _get_remote_hash(self, link):
        try:
            ident = link.get_remote_identity()
            if ident and hasattr(ident, 'hash'):
                return RNS.hexrep(ident.hash)
        except:
            pass
        try:
            if hasattr(link, 'destination') and link.destination:
                return RNS.hexrep(link.destination.hash)
        except:
            pass
        return "unknown"

    def _peer_destination_hash(self, link, fallback=None):
        try:
            ident = link.get_remote_identity()
            if ident:
                pub = ident.get_public_key()
                with RNS.Identity.known_destinations_lock:
                    for dest_hash, entry in RNS.Identity.known_destinations.items():
                        if entry[2] == pub:
                            return normalize_hash(RNS.hexrep(dest_hash))
        except Exception:
            pass
        if fallback:
            return normalize_hash(fallback)
        return normalize_hash(self._get_remote_hash(link))

    def _notify_link_established(self, link, peer_hash=None):
        peer = normalize_hash(peer_hash or self._peer_destination_hash(link))
        self.active_link = link
        self.active_peer_hash = peer
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
        self._send_peer_info(link)

    def _send_peer_info(self, link):
        if self.my_ip:
            try:
                msg = ChatMessage("__peer_info", json.dumps({"ip": self.my_ip, "port": self.my_port}))
                packet = RNS.Packet(link, msg.to_json().encode("utf-8"))
                packet.send()
                print(f"[peer_info] Sent my IP {self.my_ip}:{self.my_port} to {link.link_id.hex()[:12]}")
            except Exception as e:
                print(f"[peer_info] Failed to send: {e}")

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
        print(f"[messaging] Incoming link established: {link.link_id.hex()[:12]}")
        remote_hash = self._get_remote_hash(link)
        peer_hash = self._peer_destination_hash(link)
        self._setup_link(link)
        self._notify_link_established(link, peer_hash)
        self.drain_queue(link, remote_hash)

        if self.on_message:
            system_msg = ChatMessage("system", f"Link established with {remote_hash}")
            self.on_message(system_msg, remote_hash)

    def _link_closed(self, link):
        def callback(link):
            if link.link_id in self.links:
                del self.links[link.link_id]
            if self.active_link and self.active_link.link_id == link.link_id:
                self.active_link = None
                self.active_peer_hash = None
            if self.on_message:
                remote_hash = self._get_remote_hash(link)
                system_msg = ChatMessage("system", f"Link closed with {remote_hash}")
                self.on_message(system_msg, remote_hash)
        return callback

    def _packet_callback(self, link):
        def callback(message, packet):
            try:
                chat_msg = ChatMessage.from_json(message.decode("utf-8"))
                remote_hash = self._get_remote_hash(link)

                if chat_msg.msg_type == "__peer_info":
                    try:
                        info = json.loads(chat_msg.content)
                        self.peer_ips[link.link_id] = {"ip": info["ip"], "port": info.get("port", 8742)}
                        self.peer_ips[remote_hash] = {"ip": info["ip"], "port": info.get("port", 8742)}
                        print(f"[peer_info] Remote {remote_hash[:16]} is at {info['ip']}:{info.get('port', 8742)}")
                    except Exception as e:
                        print(f"[peer_info] Parse error: {e}")
                    return

                if chat_msg.msg_type == "__direct_offer":
                    try:
                        offer = json.loads(chat_msg.content)
                        print(f"[direct] Received offer for {offer.get('file_name')}")
                        self._handle_direct_offer(offer, link, remote_hash)
                    except:
                        pass
                    return

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

                if chat_msg.msg_type in (MESSAGE_TYPE_FILE, MESSAGE_TYPE_IMAGE, MESSAGE_TYPE_VOICE, MESSAGE_TYPE_LONGTEXT):
                    self._pending_files[link.link_id] = chat_msg
                    print(f"[messaging] Waiting for resource data for {chat_msg.file_name}...")
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

    def _resource_concluded(self, link):
        def callback(resource):
            try:
                print(f"[messaging] Resource concluded, status={resource.status}")
                if resource.status == RNS.Resource.COMPLETE:
                    chat_msg = self._pending_files.pop(link.link_id, None)
                    if chat_msg is None:
                        chat_msg = ChatMessage(MESSAGE_TYPE_FILE, "", file_name="unknown")

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
                    remote_hash = self._get_remote_hash(link)
                    if self.on_message:
                        self.on_message(chat_msg, remote_hash)
                    self._send_receipt(link, chat_msg.msg_id, "received")
                else:
                    print(f"[messaging] Resource transfer failed (status={resource.status})")
                    chat_msg = self._pending_files.pop(link.link_id, None)
                    if chat_msg and self.on_message:
                        self.on_message(
                            ChatMessage("system", f"File transfer failed: {chat_msg.file_name}"),
                            self._get_remote_hash(link)
                        )
            except Exception as e:
                print(f"[messaging] Resource concluded error: {e}")
        return callback

    def _emit_progress(self, file_name, progress, total_size=0, speed="", direction="receive", transfer_id=None, status="active"):
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

    def _has_peer_ip(self):
        if not self.active_link:
            return False
        lid = self.active_link.link_id
        if lid in self.peer_ips:
            return True
        remote = self._get_remote_hash(self.active_link)
        return remote in self.peer_ips

    def cancel_transfer(self, transfer_id=None):
        cancelled = False
        tid = transfer_id or self._current_transfer_id
        if tid and tid in self._cancel_events:
            self._cancel_events[tid].set()
            cancelled = True
        for rid, resource in list(self._active_resources.items()):
            try:
                if hasattr(resource, "cancel"):
                    resource.cancel()
                elif hasattr(resource, "close"):
                    resource.close()
                cancelled = True
            except Exception as e:
                print(f"[transfer] cancel resource {rid}: {e}")
            self._active_resources.pop(rid, None)
        if cancelled:
            self._emit_progress("", 0, status="cancelled", direction="send", transfer_id=tid)
        self._current_transfer_id = None
        return cancelled

    def _handle_direct_offer(self, offer, link, remote_hash):
        def download():
            peer = self.peer_ips.get(link.link_id)
            if not peer:
                peer = self.peer_ips.get(remote_hash)
            if not peer:
                print(f"[direct] No peer IP info for link {link.link_id.hex()[:12]} or hash {remote_hash[:16] if remote_hash else '?'}, cannot direct download")
                return

            token = offer.get("token")
            fname = offer.get("file_name", "file")
            fsize = offer.get("file_size", 0)
            msg_type = offer.get("msg_type", "file")
            transfer_id = token or fname
            url = f"http://{peer['ip']}:{peer['port']}/api/direct-transfer/{token}"
            cancel_ev = threading.Event()
            self._cancel_events[transfer_id] = cancel_ev
            self._current_transfer_id = transfer_id

            try:
                print(f"[direct] Downloading {fname} ({fsize} bytes) from {url}")
                os.makedirs(self.receive_dir, exist_ok=True)
                save_path = os.path.join(self.receive_dir, fname)
                timeout = max(600, int(fsize / 50000) + 120)
                start = time.time()
                downloaded = 0
                self._emit_progress(fname, 0, fsize, direction="receive", transfer_id=transfer_id)

                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    total = int(resp.headers.get("Content-Length", fsize) or fsize) or fsize
                    with open(save_path, "wb") as f:
                        while True:
                            if cancel_ev.is_set():
                                print(f"[direct] Download cancelled: {fname}")
                                try:
                                    os.unlink(save_path)
                                except OSError:
                                    pass
                                self._emit_progress(fname, 0, total, status="cancelled", direction="receive", transfer_id=transfer_id)
                                return
                            chunk = resp.read(262144)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            elapsed = time.time() - start
                            pct = int(downloaded * 100 / total) if total else 0
                            speed = format_speed(downloaded / elapsed) if elapsed > 0 else ""
                            self._emit_progress(fname, pct, total, speed, direction="receive", transfer_id=transfer_id)

                actual = os.path.getsize(save_path)
                print(f"[direct] Saved to {save_path} ({actual} bytes)")
                self._emit_progress(fname, 100, actual, status="complete", direction="receive", transfer_id=transfer_id)
                if self.on_message:
                    msg = ChatMessage(msg_type, save_path, file_name=fname, file_size=actual, sender=remote_hash)
                    self.on_message(msg, remote_hash)
            except Exception as e:
                print(f"[direct] HTTP transfer failed for {fname}: {e}")
                self._emit_progress(fname, 0, fsize, status="failed", direction="receive", transfer_id=transfer_id)
            finally:
                self._cancel_events.pop(transfer_id, None)
                if self._current_transfer_id == transfer_id:
                    self._current_transfer_id = None

        threading.Thread(target=download, daemon=True).start()

    def prepare_connect(self, peer_hash, peer_info=None):
        if peer_info:
            ip = peer_info.get("ip")
            port = peer_info.get("port", 8742)
            if ip:
                self._register_peer_ip(peer_hash, ip, port)

    def _register_peer_ip(self, peer_hash, ip, port=8742):
        if not ip or not peer_hash:
            return
        info = {"ip": ip, "port": port}
        clean = normalize_hash(peer_hash)
        if not clean:
            return
        self.peer_ips[clean] = info
        try:
            self.peer_ips[RNS.hexrep(bytes.fromhex(clean))] = info
        except ValueError:
            pass

    def _lookup_peer_ip(self, peer_hash):
        clean = normalize_hash(peer_hash)
        if clean in self.peer_ips:
            return self.peer_ips[clean]
        try:
            rep = RNS.hexrep(bytes.fromhex(clean))
            if rep in self.peer_ips:
                return self.peer_ips[rep]
        except ValueError:
            pass
        return None

    def _udp_interface(self):
        for iface in getattr(RNS.Transport, "interfaces", []) or []:
            if type(iface).__name__ == "UDPInterface":
                return iface
        return None

    def _bootstrap_via_http(self, dest_hash, ip, port):
        url = f"http://{ip}:{port}/api/rns-info?announce=1"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=4) as resp:
                info = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[connect] HTTP bootstrap failed for {ip}:{port}: {e}")
            return dest_hash

        remote_hex = normalize_hash(info.get("destination_hash"))
        pub_b64 = info.get("public_key", "")
        if not remote_hex or not pub_b64:
            print(f"[connect] HTTP bootstrap: incomplete rns-info from {ip}:{port}")
            return dest_hash

        try:
            remote_dest = bytes.fromhex(remote_hex)
            pub_key = base64.b64decode(pub_b64)
            RNS.Identity.remember(
                RNS.Identity.get_random_hash(),
                remote_dest,
                pub_key,
                info.get("app_data"),
            )
            self._register_peer_ip(remote_hex, ip, port)
            print(f"[connect] Remembered identity from HTTP ({ip}:{port})")
            return remote_dest
        except Exception as e:
            print(f"[connect] HTTP bootstrap remember failed: {e}")
            return dest_hash

    def _request_path(self, dest_hash):
        iface = self._udp_interface()
        if iface:
            RNS.Transport.request_path(dest_hash, on_interface=iface)
        else:
            RNS.Transport.request_path(dest_hash)

    def _wait_for_path(self, dest_hash, timeout=20):
        deadline = time.time() + timeout
        requested = False
        while time.time() < deadline:
            if RNS.Transport.has_path(dest_hash):
                return True
            if not requested:
                self._request_path(dest_hash)
                requested = True
            time.sleep(0.25)
        return RNS.Transport.has_path(dest_hash)

    def _prepare_paths(self, dest_hash, peer=None):
        self._announce()
        if self._wait_for_path(dest_hash, timeout=18):
            print(f"[connect] RNS path ready")
            return True
        if peer:
            print(f"[connect] Retrying remote announce + path...")
            self._bootstrap_via_http(dest_hash, peer["ip"], peer["port"])
            self._announce()
            if self._wait_for_path(dest_hash, timeout=12):
                print(f"[connect] RNS path ready after retry")
                return True
        print(f"[connect] No RNS UDP path — will try HTTP LAN fallback if peer IP is known")
        return False

    def _teardown_active_link(self):
        if self.active_link and self.active_link is not _HTTP_LINK:
            try:
                self.active_link.teardown()
            except Exception:
                pass
        self.active_link = None
        self.active_peer_hash = None
        self.lan_transport = "rns"
        self.http_peer = None

    def accept_http_peer(self, ip, port, peer_hash, token, name=""):
        self.lan_transport = "http"
        self.http_peer = {
            "ip": ip,
            "port": port or 8742,
            "hash": normalize_hash(peer_hash),
            "token": token,
            "name": name,
        }
        self._register_peer_ip(peer_hash, ip, port or 8742)
        self.active_link = _HTTP_LINK
        self._notify_link_established(_HTTP_LINK, peer_hash)
        print(f"[connect] Incoming HTTP LAN link from {ip}:{port}")

    def deliver_http_message(self, msg_dict, sender_hash, sender_ip, sender_port=8742):
        self._register_peer_ip(sender_hash, sender_ip, sender_port)
        try:
            chat_msg = ChatMessage.from_dict(msg_dict)
        except Exception as e:
            print(f"[http-lan] Bad message payload: {e}")
            return
        sender = normalize_hash(sender_hash)
        if chat_msg.msg_type == "__direct_offer":
            try:
                offer = json.loads(chat_msg.content)
                fake_link = _HttpOfferLink(sender_ip, sender_port)
                self._handle_direct_offer(offer, fake_link, sender)
            except Exception as e:
                print(f"[http-lan] Direct offer error: {e}")
            return
        if self.on_message:
            self.on_message(chat_msg, sender)

    def _send_lan_http(self, chat_msg):
        if not self.http_peer:
            return False
        peer = self.http_peer
        url = f"http://{peer['ip']}:{peer['port']}/api/lan-message"
        body = json.dumps({
            "token": peer.get("token", ""),
            "from_hash": self.my_dest_hash or "",
            "from_name": self.display_name or "",
            "from_port": self.my_port,
            "message": chat_msg.to_dict(),
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status != 200:
                    return False
            return True
        except Exception as e:
            print(f"[http-lan] Send failed: {e}")
            return False

    def _connect_via_http(self, peer, peer_hash):
        session_token = secrets.token_urlsafe(16)
        payload = json.dumps({
            "hash": self.my_dest_hash or "",
            "name": self.display_name or "",
            "port": self.my_port,
            "session_token": session_token,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        urls = [
            f"http://{peer['ip']}:{peer['port']}/api/lan-handshake",
            f"http://{peer['ip']}:{peer['port']}/api/rns-info",
        ]
        info = None
        last_err = None
        for url in urls:
            try:
                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=6) as resp:
                    info = json.loads(resp.read().decode("utf-8"))
                    break
            except urllib.error.HTTPError as e:
                last_err = e
                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                print(f"[connect] HTTP LAN handshake {url} -> {e.code}: {detail[:200]}")
            except Exception as e:
                last_err = e
                print(f"[connect] HTTP LAN handshake {url} failed: {e}")
        if info is None:
            if last_err and getattr(last_err, "code", None) == 404:
                print("[connect] Peer lacks HTTP LAN support — upgrade peer to chatxz 0.3.12+")
            return False
        token = info.get("token") or session_token
        if info.get("status") != "ok" or not token:
            print(f"[connect] HTTP LAN handshake rejected")
            return False
        self._teardown_active_link()
        self.lan_transport = "http"
        self.http_peer = {
            "ip": peer["ip"],
            "port": peer.get("port", 8742),
            "hash": peer_hash,
            "token": token,
            "name": info.get("name", ""),
        }
        if self.on_http_session:
            try:
                self.on_http_session(peer["ip"], token, peer_hash, peer.get("port", 8742))
            except Exception as e:
                print(f"[http-lan] session register error: {e}")
        self._register_peer_ip(peer_hash, peer["ip"], peer.get("port", 8742))
        self.active_link = _HTTP_LINK
        self._notify_link_established(_HTTP_LINK, peer_hash)
        print(f"[connect] HTTP LAN link established with {peer['ip']}:{peer['port']}")
        if self.on_message:
            self.on_message(
                ChatMessage("system", f"Connected via HTTP LAN (UDP 4242 blocked on WiFi)"),
                peer_hash,
            )
        self.drain_queue(_HTTP_LINK, peer_hash)
        return True

    def connect_to(self, destination_hash_hex):
        with self._connect_lock:
            return self._connect_to_locked(destination_hash_hex)

    def _connect_to_locked(self, destination_hash_hex):
        clean = normalize_hash(destination_hash_hex)
        if len(clean) != 32:
            print(f"[connect] Invalid hash length ({len(clean)} chars, expected 32)")
            return False
        try:
            dest_hash = bytes.fromhex(clean)
        except Exception as e:
            print(f"[connect] Invalid hash: {e}")
            return False

        print(f"[connect] Connecting to {RNS.hexrep(dest_hash)[:20]}...")
        peer = self._lookup_peer_ip(clean)

        try:
            if peer:
                print(f"[connect] Bootstrapping via HTTP {peer['ip']}:{peer['port']}...")
                dest_hash = self._bootstrap_via_http(dest_hash, peer["ip"], peer["port"])
                clean = dest_hash.hex()

            known_identity = RNS.Identity.recall(dest_hash)
            if known_identity is None:
                known_identity = RNS.Identity.recall(dest_hash, from_identity_hash=True)

            if known_identity is None:
                print(f"[connect] No known identity after bootstrap")
                if peer and self._connect_via_http(peer, clean):
                    return True
                return False

            if not self._prepare_paths(dest_hash, peer):
                if peer and self._connect_via_http(peer, clean):
                    return True
                return False

            print(f"[connect] Identity recalled successfully")
        except Exception as e:
            print(f"[connect] Identity/path setup failed: {e}")
            if peer and self._connect_via_http(peer, clean):
                return True
            return False

        try:
            destination = RNS.Destination(
                known_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                APP_NAME,
                "messages"
            )
            print(f"[connect] Destination created: {RNS.hexrep(destination.hash)[:20]}...")
        except Exception as e:
            print(f"[connect] Destination creation failed: {e}")
            return False

        self._teardown_active_link()

        try:
            link = RNS.Link(destination)
            target_peer = clean
            link.set_link_established_callback(self._outgoing_link_callback(link, target_peer))
            print(f"[connect] Link initiated, waiting for establishment...")

            for _ in range(60):
                time.sleep(0.25)
                try:
                    if link.status == RNS.Link.ACTIVE:
                        if self.active_link is None:
                            self._setup_link(link)
                            self._notify_link_established(link, target_peer)
                        print(f"[connect] Link established successfully")
                        return True
                    if link.status == RNS.Link.CLOSED:
                        print(f"[connect] Link was closed")
                        return False
                except Exception:
                    pass
                if self.active_link is not None and self.active_link.link_id == link.link_id:
                    print(f"[connect] Link established successfully")
                    return True

            if peer and self._connect_via_http(peer, clean):
                return True
            print(f"[connect] Link establishment timed out (check UDP 4242 firewall on both peers)")
            return False
        except Exception as e:
            print(f"[connect] Link creation failed: {e}")
            if peer and self._connect_via_http(peer, clean):
                return True
            return False

    def _outgoing_link_callback(self, link, peer_hash):
        def callback(link):
            remote_hash = self._get_remote_hash(link)
            print(f"[messaging] Outgoing link established: {link.link_id.hex()[:12]} -> {remote_hash[:16]}...")
            self._setup_link(link)
            self._notify_link_established(link, peer_hash)
            self.drain_queue(link, remote_hash)
            if self.on_message:
                self.on_message(
                    ChatMessage("system", f"Connected to {remote_hash}"),
                    remote_hash
                )
        return callback

    def send_message(self, text, receipt_callback=None):
        if not self.active_link:
            print("[messaging] send_message: no active link")
            return False
        msg = ChatMessage(MESSAGE_TYPE_TEXT, text)
        if self.lan_transport == "http":
            if self._send_lan_http(msg):
                print(f"[http-lan] Sent text: {text[:50]}...")
                self._sent_messages[msg.msg_id] = msg
                if receipt_callback:
                    receipt_callback("sent")
                return msg
            return False
        data = msg.to_json().encode("utf-8")
        mtu = getattr(self.active_link, 'mtu', 500)
        try:
            if len(data) > mtu - 50:
                return self._send_long_text(msg, text, data, receipt_callback)
            packet = RNS.Packet(self.active_link, data)
            packet.send()
            print(f"[messaging] Sent text message: {text[:50]}...")
            self._sent_messages[msg.msg_id] = msg
            if receipt_callback:
                self._receipt_callbacks[msg.msg_id] = receipt_callback
            return msg
        except Exception as e:
            print(f"[messaging] Send failed: {e}")
            return False

    def _send_long_text(self, msg, text, data, receipt_callback):
        import tempfile as _tf
        tmp = _tf.NamedTemporaryFile(delete=False, suffix=".txt", mode="w")
        tmp.write(text)
        tmp_path = tmp.name
        tmp.close()
        meta = ChatMessage(MESSAGE_TYPE_LONGTEXT, json.dumps({"msg_id": msg.msg_id, "file_name": "longtext.txt"}))
        try:
            packet = RNS.Packet(self.active_link, meta.to_json().encode("utf-8"))
            packet.send()
        except Exception as e:
            print(f"[messaging] Long text metadata send failed: {e}")
            os.unlink(tmp_path)
            return False
        try:
            f = open(tmp_path, "rb")
            RNS.Resource(f, self.active_link, callback=self._resource_send_callback("longtext"),
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

    def send_file(self, file_path, msg_type=MESSAGE_TYPE_FILE, progress_callback=None):
        if not self.active_link or not os.path.exists(file_path):
            return False
        with self._file_send_lock:
            fname = os.path.basename(file_path)
            fsize = os.path.getsize(file_path)
            chat_msg = ChatMessage(msg_type, str(time.time()), file_name=fname, file_size=fsize)
            transfer_id = chat_msg.msg_id
            self._current_transfer_id = transfer_id
            cancel_ev = threading.Event()
            self._cancel_events[transfer_id] = cancel_ev
            try:
                packet = RNS.Packet(self.active_link, chat_msg.to_json().encode("utf-8"))
                packet.send()

                def wrapped_progress(resource):
                    if cancel_ev.is_set():
                        return
                    if progress_callback:
                        progress_callback(resource)
                    try:
                        pct = int(resource.get_progress() * 100)
                        self._emit_progress(fname, pct, fsize, direction="send", transfer_id=transfer_id)
                    except Exception:
                        pass

                f = open(file_path, "rb")
                resource = RNS.Resource(f, self.active_link,
                             callback=self._resource_send_callback(fname, transfer_id, fsize),
                             progress_callback=wrapped_progress,
                             auto_compress=False)
                self._active_resources[transfer_id] = resource
                print(f"[messaging] Sent file: {fname} ({fsize} bytes)")
                self._sent_messages[chat_msg.msg_id] = chat_msg
                return chat_msg
            except Exception as e:
                print(f"[messaging] File send failed: {e}")
                self._emit_progress(fname, 0, fsize, status="failed", direction="send", transfer_id=transfer_id)
                self._cancel_events.pop(transfer_id, None)
                self._active_resources.pop(transfer_id, None)
                return False

    def direct_send_file(self, file_path, msg_type=MESSAGE_TYPE_FILE):
        if not self.active_link or not os.path.exists(file_path):
            return False
        fname = os.path.basename(file_path)
        fsize = os.path.getsize(file_path)
        token = os.urandom(16).hex()
        transfer_id = token
        self._current_transfer_id = transfer_id
        self.direct_transfer_tokens[token] = {
            "path": file_path,
            "time": time.time(),
            "size": fsize,
            "name": fname,
            "transfer_id": transfer_id,
        }
        threading.Timer(3600, lambda: self.direct_transfer_tokens.pop(token, None)).start()
        offer = json.dumps({
            "type": "__direct_offer",
            "token": token,
            "file_name": fname,
            "file_size": fsize,
            "msg_type": msg_type,
        })
        try:
            offer_msg = ChatMessage("__direct_offer", offer)
            if self.lan_transport == "http":
                if not self._send_lan_http(offer_msg):
                    raise RuntimeError("HTTP LAN offer failed")
            else:
                packet = RNS.Packet(self.active_link, offer_msg.to_json().encode("utf-8"))
                packet.send()
            print(f"[direct] Sent file offer: {fname} ({fsize} bytes)")
            self._emit_progress(fname, 0, fsize, direction="send", transfer_id=transfer_id, status="direct")
            return ChatMessage(msg_type, file_path, file_name=fname, file_size=fsize, msg_id=transfer_id)
        except Exception as e:
            print(f"[direct] File offer send failed: {e}")
            self._emit_progress(fname, 0, fsize, status="failed", direction="send", transfer_id=transfer_id)
            return False

    def send_file_smart(self, file_path, msg_type=MESSAGE_TYPE_FILE, progress_callback=None):
        if not self.active_link or not os.path.exists(file_path):
            return False
        fsize = os.path.getsize(file_path)
        use_direct = fsize >= DIRECT_TRANSFER_THRESHOLD or msg_type in (MESSAGE_TYPE_FILE, MESSAGE_TYPE_IMAGE)
        if use_direct:
            for _ in range(15):
                if self._has_peer_ip():
                    break
                time.sleep(0.2)
            if self._has_peer_ip():
                result = self.direct_send_file(file_path, msg_type)
                if result:
                    return result
                print(f"[transfer] Direct send failed for {os.path.basename(file_path)}, falling back to RNS resource")
        return self.send_file(file_path, msg_type, progress_callback=progress_callback)

    def _resource_send_callback(self, fname, transfer_id=None, fsize=0):
        def callback(resource):
            print(f"[messaging] File transfer complete: {fname}")
            self._active_resources.pop(transfer_id, None)
            self._cancel_events.pop(transfer_id, None)
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


class _HttpOfferLink:
    def __init__(self, ip, port):
        self.link_id = f"http:{ip}:{port}".encode()
