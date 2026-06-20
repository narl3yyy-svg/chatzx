import os
import RNS

IDENTITY_DIR = "identities"
IDENTITY_FILE = "identity"

class IdentityManager:
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.identity_dir = os.path.join(config_dir, IDENTITY_DIR)
        self.identity_path = os.path.join(self.identity_dir, IDENTITY_FILE)
        self.identity = None

    def load_or_create(self):
        os.makedirs(self.identity_dir, exist_ok=True)
        if os.path.exists(self.identity_path):
            self.identity = RNS.Identity.from_file(self.identity_path)
        else:
            self.identity = RNS.Identity()
            self.identity.to_file(self.identity_path)
        return self.identity

    def get_hash(self):
        if self.identity:
            return self.identity.hash
        return None

    def get_hex_hash(self):
        h = self.get_hash()
        if h:
            return RNS.hexrep(h)
        return None
