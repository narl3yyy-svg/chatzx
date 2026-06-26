import os
import sys
import time
import subprocess
from datetime import datetime
import RNS

from chatxz.core.identity import IdentityManager
from chatxz.core.messaging import MessagingBackend
from chatxz.core.voice import VoiceRecorder, VoicePlayer
from chatxz.utils.helpers import get_config_dir, get_data_dir, format_size, truncate_hash

CONFIG_DIR = get_config_dir()
DATA_DIR = get_data_dir()

class ChatxzApp:
    def __init__(self):
        self.config_dir = CONFIG_DIR
        self.data_dir = DATA_DIR
        os.makedirs(self.config_dir, exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)

        self.identity_mgr = IdentityManager(self.config_dir)
        self.identity = self.identity_mgr.load_or_create()
        self.messaging = None
        self.voice_recorder = None
        self.messages = []
        self.connected_hash = None
        self.running = False
        self._rns_initialized = False

    def start(self):
        if not self._rns_initialized:
            from chatxz.core.rns_tuning import apply_chatxz_rns_tuning
            apply_chatxz_rns_tuning()
            RNS.Reticulum(self.config_dir)
            self._rns_initialized = True

        self.messaging = MessagingBackend(
            self.identity,
            self.config_dir,
            on_message=self._on_message
        )
        self.voice_recorder = VoiceRecorder(self.config_dir)

        dest = self.messaging.start()
        my_hash = RNS.hexrep(dest.hash)
        return my_hash

    def stop(self):
        self.running = False
        if self.messaging:
            self.messaging.stop()

    def _on_message(self, chat_msg, sender_hash):
        self.messages.append((chat_msg, sender_hash))
        self._display_message(chat_msg, sender_hash)

    def _display_message(self, chat_msg, sender_hash):
        timestamp = datetime.fromtimestamp(chat_msg.timestamp).strftime("%H:%M:%S")
        sender = truncate_hash(sender_hash) if sender_hash else "System"

        if chat_msg.msg_type == "system":
            print(f"\n[{timestamp}] {chat_msg.content}")
        elif chat_msg.msg_type in ("text", "emoji"):
            print(f"\n[{timestamp}] {sender}: {chat_msg.content}")
        elif chat_msg.msg_type == "image":
            fname = chat_msg.file_name or "image"
            print(f"\n[{timestamp}] {sender}: [Image] {fname}")
            if os.path.exists(chat_msg.content):
                self._display_image_in_term(chat_msg.content)
        elif chat_msg.msg_type == "voice":
            fname = chat_msg.file_name or "voice"
            print(f"\n[{timestamp}] {sender}: [Voice] {fname}")
        elif chat_msg.msg_type == "video":
            fname = chat_msg.file_name or "video"
            fsize = format_size(chat_msg.file_size or 0)
            print(f"\n[{timestamp}] {sender}: [Video] {fname} ({fsize})")
        elif chat_msg.msg_type == "file":
            fname = chat_msg.file_name or "file"
            fsize = format_size(chat_msg.file_size or 0)
            print(f"\n[{timestamp}] {sender}: [File] {fname} ({fsize})")
        sys.stdout.flush()

    def _display_image_in_term(self, img_path):
        try:
            term = os.environ.get("TERM", "")
            if "kitty" in term:
                self._kitty_icat(img_path)
            elif os.environ.get("TERMINAL_EMULATOR") == "Konsole" or "sixel" in term:
                self._sixel_print(img_path)
            else:
                self._chafa_print(img_path)
        except:
            pass

    def _kitty_icat(self, img_path):
        try:
            subprocess.run(["kitty", "+kitten", "icat", img_path],
                         capture_output=True, timeout=5)
        except:
            pass

    def _sixel_print(self, img_path):
        try:
            subprocess.run(["convert", img_path, "sixel:-"],
                         capture_output=True, timeout=5)
        except:
            pass

    def _chafa_print(self, img_path):
        try:
            result = subprocess.run(["chafa", "--symbols", "block", img_path],
                                  capture_output=True, timeout=5)
            if result.returncode == 0:
                print(result.stdout.decode())
        except:
            pass

    def connect(self, peer_hash):
        if self.messaging:
            ok = self.messaging.connect_to(peer_hash, user_initiated=True)
            if ok:
                self.connected_hash = peer_hash
            return ok
        return False

    def send_text(self, text):
        if self.messaging:
            return self.messaging.send_message(text)
        return False

    def send_file(self, file_path):
        if not self.messaging or not self.messaging.active_link:
            print("Not connected to anyone")
            return False
        from chatxz.utils.helpers import media_type_for_filename
        msg_type = media_type_for_filename(file_path)
        return self.messaging.send_file(file_path, msg_type)

    def send_voice(self, duration=None):
        if not self.messaging or not self.messaging.active_link:
            print("Not connected to anyone")
            return False
        print("Recording voice... (Ctrl+C to stop)")
        file_path = self.voice_recorder.start_recording()
        if not file_path:
            print("No microphone available")
            return False
        try:
            if duration:
                time.sleep(duration)
                self.voice_recorder.stop_recording()
            else:
                input("Press Enter to stop recording...")
                self.voice_recorder.stop_recording()
        except KeyboardInterrupt:
            self.voice_recorder.stop_recording()

        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            print(f"Recording saved, sending...")
            return self.messaging.send_file(file_path, "voice")
        return False

    def play_voice(self, file_path):
        if os.path.exists(file_path):
            VoicePlayer.play(file_path)

    def list_contacts(self):
        contacts_dir = os.path.join(self.config_dir, "contacts")
        os.makedirs(contacts_dir, exist_ok=True)
        contacts = []
        for f in os.listdir(contacts_dir):
            path = os.path.join(contacts_dir, f)
            try:
                with open(path) as fh:
                    name = fh.read().strip()
                    contacts.append((f, name))
            except:
                contacts.append((f, f))
        return contacts

    def add_contact(self, peer_hash, name):
        contacts_dir = os.path.join(self.config_dir, "contacts")
        os.makedirs(contacts_dir, exist_ok=True)
        path = os.path.join(contacts_dir, peer_hash)
        with open(path, "w") as f:
            f.write(name)

    def get_my_hash(self):
        return self.identity_mgr.get_hex_hash()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="chatxz - Reticulum based chat")
    parser.add_argument("--connect", "-c", help="Connect to peer identity hash")
    parser.add_argument("--send", "-s", help="Send a text message")
    parser.add_argument("--file", "-f", help="Send a file")
    parser.add_argument("--voice", "-v", action="store_true", help="Record and send voice")
    parser.add_argument("--daemon", "-d", action="store_true", help="Run as daemon (listen only)")
    parser.add_argument("--list", "-l", action="store_true", help="List contacts")
    parser.add_argument("--add-contact", help="Add a contact (hash:name)")
    args = parser.parse_args()

    app = ChatxzApp()
    my_hash = app.start()
    print(f"chatxz v0.1.0")
    print(f"Your identity: {my_hash}")
    print("---")

    if args.add_contact:
        parts = args.add_contact.split(":", 1)
        if len(parts) == 2:
            app.add_contact(parts[0], parts[1])
            print(f"Contact added: {parts[0]} -> {parts[1]}")
        return

    if args.list:
        contacts = app.list_contacts()
        if contacts:
            print("Contacts:")
            for hash_str, name in contacts:
                print(f"  {hash_str} - {name}")
        else:
            print("No contacts yet")
        return

    if args.connect:
        app.connect(args.connect)
        time.sleep(1)

    if args.send:
        app.send_text(args.send)
        print("Message sent")
        app.stop()
        return

    if args.file:
        app.send_file(args.file)
        print("File sent")
        app.stop()
        return

    if args.voice:
        app.send_voice()
        app.stop()
        return

    if args.daemon:
        print(f"Listening for incoming connections as {my_hash}...")
        sys.stdout.flush()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        app.stop()
        return

    print("Interactive mode. Commands:")
    print("  /connect <hash>  - Connect to a peer")
    print("  /send <text>     - Send a message")
    print("  /file <path>     - Send a file")
    print("  /voice           - Record and send voice")
    print("  /play <path>     - Play a voice note")
    print("  /contacts        - List contacts")
    print("  /add <hash:name> - Add a contact")
    print("  /help            - Show this help")
    print("  /quit            - Exit")
    print("  /myid            - Show your identity")
    print("---")

    try:
        while True:
            cmd = input("> ").strip()
            if not cmd:
                continue
            if cmd == "/quit":
                break
            elif cmd == "/help":
                print("Commands: /connect <hash>, /send <text>, /file <path>, /voice, /play <path>, /contacts, /add <hash:name>, /myid, /help, /quit")
            elif cmd == "/myid":
                print(f"Your identity: {my_hash}")
            elif cmd.startswith("/connect "):
                peer = cmd[9:].strip()
                app.connect(peer)
            elif cmd.startswith("/send "):
                text = cmd[6:]
                if app.send_text(text):
                    print("Sent")
                else:
                    print("Failed - not connected")
            elif cmd.startswith("/file "):
                path = cmd[6:].strip()
                app.send_file(path)
            elif cmd == "/voice":
                app.send_voice()
            elif cmd.startswith("/play "):
                path = cmd[6:].strip()
                app.play_voice(path)
            elif cmd == "/contacts":
                contacts = app.list_contacts()
                if contacts:
                    for h, n in contacts:
                        print(f"  {h} - {n}")
                else:
                    print("No contacts")
            elif cmd.startswith("/add "):
                parts = cmd[5:].strip().split(":", 1)
                if len(parts) == 2:
                    app.add_contact(parts[0], parts[1])
                    print(f"Contact added: {parts[0]} -> {parts[1]}")
                else:
                    print("Usage: /add <hash:name>")
            else:
                if app.send_text(cmd):
                    pass
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] You: {cmd}")
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()

if __name__ == "__main__":
    main()
