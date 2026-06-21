import threading
import RNS
import json
import time
import base64
import os
import tempfile
from datetime import datetime

APP_NAME = "chatxz"
ANNOUNCE_INTERVAL = 30

MESSAGE_TYPE_TEXT = "text"
MESSAGE_TYPE_FILE = "file"
MESSAGE_TYPE_IMAGE = "image"
MESSAGE_TYPE_VOICE = "voice"
MESSAGE_TYPE_EMOJI = "emoji"

class ChatMessage:
    def __init__(self, msg_type, content, sender=None, timestamp=None, file_name=None, file_size=None):
        self.msg_type = msg_type
        self.content = content
        self.sender = sender
        self.timestamp = timestamp or time.time()
        self.file_name = file_name
        self.file_size = file_size

    def to_dict(self):
        d = {
            "type": self.msg_type,
            "content": self.content,
            "timestamp": self.timestamp,
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
        )

    def to_json(self):
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, data):
        return cls.from_dict(json.loads(data))

class MessagingBackend:
    def __init__(self, identity, config_dir, on_message=None, on_file=None,
                 display_name="", announce_interval=30, auto_announce=False):
        self.identity = identity
        self.config_dir = config_dir
        self.on_message = on_message
        self.on_file = on_file
        self.display_name = display_name
        self.announce_interval = announce_interval
        self.auto_announce = auto_announce
        self.destination = None
        self.links = {}
        self.active_link = None
        self.running = False
        self._announce_thread = None

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
            if ident:
                return RNS.hexrep(ident.hash)
        except:
            pass
        return "unknown"

    def _link_callback(self, link):
        print(f"[messaging] Incoming link established: {link.link_id}")
        remote_hash = self._get_remote_hash(link)
        self.links[link.link_id] = link
        link.set_link_closed_callback(self._link_closed(link))
        link.set_packet_callback(self._packet_callback(link))

        if self.on_message:
            system_msg = ChatMessage("system", f"Link established with {remote_hash}")
            self.on_message(system_msg, remote_hash)

    def _link_closed(self, link):
        def callback(link):
            if link.link_id in self.links:
                del self.links[link.link_id]
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
                chat_msg.sender = remote_hash
                print(f"[messaging] Received {chat_msg.msg_type} from {remote_hash[:12]}...")

                if chat_msg.msg_type in (MESSAGE_TYPE_FILE, MESSAGE_TYPE_IMAGE, MESSAGE_TYPE_VOICE):
                    self._receive_file_resource(link, chat_msg, remote_hash)
                elif self.on_message:
                    self.on_message(chat_msg, remote_hash)
            except Exception as e:
                print(f"[messaging] Packet callback error: {e}")
                if self.on_message:
                    self.on_message(
                        ChatMessage("system", f"Failed to parse message: {e}"),
                        None
                    )
        return callback

    def _receive_file_resource(self, link, chat_msg, remote_hash):
        def resource_callback(resource):
            try:
                ext_map = {
                    MESSAGE_TYPE_IMAGE: ".png",
                    MESSAGE_TYPE_VOICE: ".opus",
                    MESSAGE_TYPE_FILE: ".file",
                }
                ext = ext_map.get(chat_msg.msg_type, ".file")
                receive_dir = os.path.join(self.config_dir, "received")
                os.makedirs(receive_dir, exist_ok=True)
                fname = chat_msg.file_name or f"{chat_msg.msg_type}_{int(time.time())}{ext}"
                save_path = os.path.join(receive_dir, fname)

                if resource.write_to_file(save_path):
                    chat_msg.content = save_path
                    chat_msg.file_name = fname
                    if self.on_message:
                        self.on_message(chat_msg, remote_hash)
            except Exception as e:
                if self.on_message:
                    self.on_message(
                        ChatMessage("system", f"File receive failed: {e}"),
                        remote_hash
                    )
        RNS.Resource.load_resource(link, resource_callback)

    def connect_to(self, destination_hash_hex):
        try:
            clean = destination_hash_hex.replace("<", "").replace(">", "").strip()
            dest_hash = bytes.fromhex(clean.replace(":", ""))
        except Exception as e:
            print(f"[connect] Invalid hash: {e}")
            return False

        print(f"[connect] Connecting to {RNS.hexrep(dest_hash)[:20]}...")

        try:
            known_identity = RNS.Identity.recall(dest_hash)
            if known_identity is None:
                print(f"[connect] No known identity, requesting path...")
                if not RNS.Transport.has_path(dest_hash):
                    RNS.Transport.request_path(dest_hash)
                    for _ in range(10):
                        time.sleep(0.5)
                        if RNS.Transport.has_path(dest_hash):
                            break
                    if not RNS.Transport.has_path(dest_hash):
                        print(f"[connect] No path to destination")
                        return False
                known_identity = RNS.Identity.recall(dest_hash)
                if known_identity is None:
                    print(f"[connect] Could not recall identity after path request")
                    return False
            print(f"[connect] Identity recalled successfully")
        except Exception as e:
            print(f"[connect] Identity recall failed: {e}")
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

        try:
            link = RNS.Link(destination)
            link.set_link_established_callback(self._outgoing_link_callback(link))
            print(f"[connect] Link initiated, waiting for establishment...")

            for _ in range(20):
                time.sleep(0.25)
                if self.active_link is not None and self.active_link.link_id == link.link_id:
                    print(f"[connect] Link established successfully")
                    return True
                try:
                    if link.status == RNS.Link.CLOSED:
                        print(f"[connect] Link was closed")
                        return False
                except:
                    pass

            if self.active_link is not None:
                print(f"[connect] Link established (timeout)")
                return True
            print(f"[connect] Link establishment timed out")
            return False
        except Exception as e:
            print(f"[connect] Link creation failed: {e}")
            return False

    def _outgoing_link_callback(self, link):
        def callback(link):
            remote_hash = self._get_remote_hash(link)
            print(f"[messaging] Outgoing link established: {link.link_id} -> {remote_hash[:12]}...")
            self.links[link.link_id] = link
            link.set_link_closed_callback(self._link_closed(link))
            link.set_packet_callback(self._packet_callback(link))
            self.active_link = link
            if self.on_message:
                self.on_message(
                    ChatMessage("system", f"Connected to {remote_hash}"),
                    remote_hash
                )
        return callback

    def send_message(self, text):
        if not self.active_link:
            print("[messaging] send_message: no active link")
            return False
        msg = ChatMessage(MESSAGE_TYPE_TEXT, text)
        try:
            packet = RNS.Packet(self.active_link, msg.to_json().encode("utf-8"))
            packet.send()
            print(f"[messaging] Sent text message: {text[:50]}...")
            return True
        except Exception as e:
            print(f"[messaging] Send failed: {e}")
            return False

    def send_file(self, file_path, msg_type=MESSAGE_TYPE_FILE):
        if not self.active_link or not os.path.exists(file_path):
            return False
        fname = os.path.basename(file_path)
        fsize = os.path.getsize(file_path)
        chat_msg = ChatMessage(msg_type, str(time.time()), file_name=fname, file_size=fsize)
        try:
            packet = RNS.Packet(self.active_link, chat_msg.to_json().encode("utf-8"))
            packet.send()
            resource = RNS.Resource(file_path, self.active_link, callback=self._resource_send_callback(file_path))
            return True
        except Exception as e:
            print(f"[messaging] File send failed: {e}")
            return False

    def _resource_send_callback(self, file_path):
        def callback(resource):
            pass
        return callback
