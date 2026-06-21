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
    def __init__(self, identity, config_dir, on_message=None, on_file=None):
        self.identity = identity
        self.config_dir = config_dir
        self.on_message = on_message
        self.on_file = on_file
        self.destination = None
        self.links = {}
        self.active_link = None
        self.running = False

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
        self._announce()
        self._announce_thread = threading.Thread(target=self._announce_loop, daemon=True)
        self._announce_thread.start()
        self.running = True
        return self.destination

    def _announce(self):
        if self.destination:
            announce_data = json.dumps({"app": APP_NAME, "name": ""}).encode("utf-8")
            self.destination.announce(app_data=announce_data)

    def _announce_loop(self):
        while True:
            for _ in range(ANNOUNCE_INTERVAL):
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

    def _link_callback(self, link):
        remote_hash = RNS.hexrep(link.get_remote_identity().hash)
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
                remote_hash = RNS.hexrep(link.get_remote_identity().hash)
                system_msg = ChatMessage("system", f"Link closed with {remote_hash}")
                self.on_message(system_msg, remote_hash)
        return callback

    def _packet_callback(self, link):
        def callback(message, packet):
            try:
                chat_msg = ChatMessage.from_json(message.decode("utf-8"))
                remote_hash = RNS.hexrep(link.get_remote_identity().hash)
                chat_msg.sender = remote_hash

                if chat_msg.msg_type in (MESSAGE_TYPE_FILE, MESSAGE_TYPE_IMAGE, MESSAGE_TYPE_VOICE):
                    transfer_id = chat_msg.content
                    self._receive_file_resource(link, chat_msg, remote_hash)
                elif self.on_message:
                    self.on_message(chat_msg, remote_hash)
            except Exception as e:
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
        except:
            return False

        try:
            known_identity = RNS.Identity.recall(dest_hash)
            if known_identity is None:
                RNS.log(f"[connect] No known identity for {destination_hash_hex}, requesting...")
                RNS.Identity.request(dest_hash)
                time.sleep(1.0)
                known_identity = RNS.Identity.recall(dest_hash)
                if known_identity is None:
                    RNS.log(f"[connect] Could not recall identity for {destination_hash_hex}")
                    return False
        except Exception as e:
            RNS.log(f"[connect] Identity recall failed: {e}")
            return False

        destination = RNS.Destination(
            known_identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            APP_NAME,
            "messages"
        )

        try:
            dest_hash_check = RNS.hexrep(destination.hash)
            RNS.log(f"[connect] Created destination {dest_hash_check[:12]}...")
            link = RNS.Link(destination)
            link.set_link_established_callback(self._outgoing_link_callback(link))
            RNS.log(f"[connect] Link initiated, waiting for establishment...")
            return True
        except Exception as e:
            RNS.log(f"[connect] Link failed: {e}")
            return False

    def _outgoing_link_callback(self, link):
        def callback(link):
            remote_hash = RNS.hexrep(link.get_remote_identity().hash)
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
            return False
        msg = ChatMessage(MESSAGE_TYPE_TEXT, text)
        try:
            packet = RNS.Packet(self.active_link, msg.to_json().encode("utf-8"))
            packet.send()
            return True
        except:
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
            return False

    def _resource_send_callback(self, file_path):
        def callback(resource):
            pass
        return callback
