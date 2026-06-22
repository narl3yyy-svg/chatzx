import threading, RNS, json, time, os, tempfile, uuid
from chatxz.utils.helpers import format_speed
from chatxz.core.discovery import normalize_hash

APP_NAME = "chatxz"

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
                 on_progress=None, on_link_established=None,
                 display_name="", auto_announce=False,
                 receive_dir=None):
        self.identity = identity
        self.config_dir = config_dir
        self.receive_dir = receive_dir or os.path.join(config_dir, "received")
        self.on_message = on_message
        self.on_file = on_file
        self.on_progress = on_progress
        self.on_link_established = on_link_established
        self.display_name = display_name
        self.auto_announce = auto_announce
        self.announce_interval = 30
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
        self._cancelled_transfers = set()
        self._current_transfer_id = None
        self._progress_last = {}
        self._progress_throttle_s = 0.25
        self.my_dest_hash = None
        self.identity_to_dest = {}
        self.dest_to_identity = {}
        self._send_link = None

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
        return bool(a and b and a == b)

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
        ident = link.get_remote_identity()
        if ident and hasattr(ident, 'hash') and ident.hash:
            try:
                hash_input = ident.hash + APP_NAME.encode("utf-8") + b"messages"
                dest_hash = RNS.Identity.truncated_hash(hash_input)
                return RNS.hexrep(dest_hash)
            except Exception:
                pass
            try:
                pub = ident.get_public_key()
                if pub:
                    with RNS.Identity.known_destinations_lock:
                        for dest_hash_bytes, entry in RNS.Identity.known_destinations.items():
                            if len(entry) > 2 and entry[2] == pub:
                                return RNS.hexrep(dest_hash_bytes)
            except Exception:
                pass
            return RNS.hexrep(ident.hash)
        try:
            if hasattr(link, 'destination') and link.destination:
                return RNS.hexrep(link.destination.hash)
        except:
            pass
        return "unknown"

    def _peer_destination_hash(self, link, fallback=None):
        ident_hex = ""
        try:
            ident = link.get_remote_identity()
            if ident:
                ident_hex = normalize_hash(RNS.hexrep(ident.hash))
                pub = ident.get_public_key()
                with RNS.Identity.known_destinations_lock:
                    for dest_hash, entry in RNS.Identity.known_destinations.items():
                        if entry[2] == pub:
                            dest = normalize_hash(RNS.hexrep(dest_hash))
                            self.register_peer_mapping(dest, ident_hex)
                            return dest
        except Exception:
            pass
        if fallback:
            return self.dest_hash_for(fallback)
        return self.dest_hash_for(self._get_remote_hash(link))

    def _notify_link_established(self, link, peer_hash=None):
        peer = self.dest_hash_for(peer_hash or self._peer_destination_hash(link))
        self.active_link = link
        self.active_peer_hash = peer
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
        peer_hash = self._peer_destination_hash(link)
        if self.active_link and self.hashes_equivalent(peer_hash, self.active_peer_hash):
            print(f"[messaging] Ignoring duplicate incoming link from {peer_hash[:16]}...")
            try:
                link.teardown()
            except Exception:
                pass
            return
        if self._session_occupied(peer_hash):
            print(f"[messaging] Rejecting incoming link from {peer_hash[:16]}... (busy with {self.active_peer_hash[:16]}...)")
            try:
                link.teardown()
            except Exception:
                pass
            return
        print(f"[messaging] Incoming link established: {link.link_id.hex()[:12]}")
        self._setup_link(link)
        self._notify_link_established(link, peer_hash)
        self.drain_queue(link, peer_hash)

        if self.on_message:
            system_msg = ChatMessage("system", f"Link established with {peer_hash}")
            self.on_message(system_msg, peer_hash)

    def _link_closed(self, link):
        def callback(link):
            if link.link_id in self.links:
                del self.links[link.link_id]
            if self.active_link and self.active_link.link_id == link.link_id:
                self.active_link = None
                self.active_peer_hash = None
            if self._send_link and self._send_link.link_id == link.link_id:
                self._send_link = self.active_link
            if self.on_message:
                remote_hash = self.dest_hash_for(self._peer_destination_hash(link))
                system_msg = ChatMessage("system", f"Link closed with {remote_hash}")
                self.on_message(system_msg, remote_hash)
        return callback

    def _packet_callback(self, link):
        def callback(message, packet):
            try:
                chat_msg = ChatMessage.from_json(message.decode("utf-8"))
                remote_hash = self.dest_hash_for(self._peer_destination_hash(link))

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
                    remote_hash = self.dest_hash_for(self._peer_destination_hash(link))
                    if self.on_message:
                        self.on_message(chat_msg, remote_hash)
                    self._send_receipt(link, chat_msg.msg_id, "received")
                else:
                    print(f"[messaging] Resource transfer failed (status={resource.status})")
                    with self._pending_lock:
                        queue = self._pending_files.get(link.link_id, [])
                        chat_msg = queue.pop(0) if queue else None
                    if chat_msg and self.on_message:
                        self.on_message(
                            ChatMessage("system", f"File transfer failed: {chat_msg.file_name}"),
                            self.dest_hash_for(self._peer_destination_hash(link))
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

    def cancel_transfer(self, transfer_id=None):
        cancelled = False
        tid = transfer_id or self._current_transfer_id
        if tid:
            self._cancelled_transfers.add(tid)
        if tid and tid in self._cancel_events:
            self._cancel_events[tid].set()
            cancelled = True
        targets = list(self._active_resources.items())
        if tid:
            targets = [(rid, res) for rid, res in targets if rid == tid]
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
            self._active_resources.pop(rid, None)
            self._cancel_events.pop(rid, None)
        if not targets and tid:
            cancelled = True
        if cancelled:
            fname = ""
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

    def _teardown_active_link(self):
        if self.active_link:
            try:
                self.active_link.teardown()
            except Exception:
                pass
        self.active_link = None
        self.active_peer_hash = None
        self._send_link = None

    def _interrupted(self):
        return self.shutdown_requested or not self.running

    def connect_to(self, destination_hash_hex):
        with self._connect_lock:
            if self._interrupted():
                return False

            clean = normalize_hash(destination_hash_hex)
            if len(clean) != 32:
                print(f"[connect] Invalid hash length ({len(clean)} chars, expected 32)")
                return False
            try:
                dest_hash = bytes.fromhex(clean)
            except Exception as e:
                print(f"[connect] Invalid hash: {e}")
                return False

            if self.active_link and self.active_peer_hash and normalize_hash(clean) != normalize_hash(self.active_peer_hash):
                self._teardown_active_link()

            known_identity = RNS.Identity.recall(dest_hash)
            if known_identity is None:
                known_identity = RNS.Identity.recall(dest_hash, from_identity_hash=True)
            if known_identity is None:
                print(f"[connect] No known identity for {RNS.hexrep(dest_hash)[:20]}...")
                print("[connect] Make sure the peer has announced first, or try again later")
                return False

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

            self._announce()
            self._teardown_active_link()
            print(f"[connect] Connecting to {RNS.hexrep(dest_hash)[:20]}...")

            deadline = time.time() + 60
            attempt = 0
            while time.time() < deadline:
                if self._interrupted():
                    print("[connect] Aborted (shutdown)")
                    return False
                attempt += 1
                print(f"[connect] Link attempt {attempt}...")
                try:
                    link = RNS.Link(destination)
                    for _ in range(60):
                        if self._interrupted():
                            try:
                                link.teardown()
                            except:
                                pass
                            return False
                        time.sleep(0.25)
                        try:
                            if link.status == RNS.Link.ACTIVE:
                                self._setup_link(link)
                                self._notify_link_established(link, clean)
                                self._send_link = link
                                print(f"[connect] Link established successfully")
                                self.drain_queue(link, clean)
                                return True
                            if link.status == RNS.Link.CLOSED:
                                break
                        except:
                            pass
                        if self.active_link and self.active_link.link_id == link.link_id:
                            print(f"[connect] Link established successfully")
                            return True
                    try:
                        link.teardown()
                    except:
                        pass
                except Exception as e:
                    print(f"[connect] Link attempt {attempt} failed: {e}")

                if time.time() < deadline:
                    remaining = int(deadline - time.time())
                    wait = min(15, remaining)
                    print(f"[connect] Retrying in {wait}s... ({remaining}s remaining)")
                    for _ in range(wait * 4):
                        if self._interrupted():
                            return False
                        time.sleep(0.25)

            print(f"[connect] Link establishment timed out after {attempt} attempt(s)")
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

    def send_file(self, file_path, msg_type=MESSAGE_TYPE_FILE, progress_callback=None):
        link = self._outgoing_link()
        if not link or not os.path.exists(file_path):
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
                self._cancel_events.pop(transfer_id, None)
                self._active_resources.pop(transfer_id, None)
                return False

    def _resource_send_callback(self, fname, transfer_id=None, fsize=0):
        def callback(resource):
            self._active_resources.pop(transfer_id, None)
            self._cancel_events.pop(transfer_id, None)
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
