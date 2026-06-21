import os
import RNS
import threading
import time

TRANSFER_DIR = "transfers"

class FileTransfer:
    def __init__(self, config_dir, status_callback=None):
        self.config_dir = config_dir
        self.transfer_dir = os.path.join(config_dir, TRANSFER_DIR)
        os.makedirs(self.transfer_dir, exist_ok=True)
        self.status_callback = status_callback
        self.active_transfers = {}

    def send(self, link, file_path):
        if not os.path.exists(file_path):
            return False
        fname = os.path.basename(file_path)
        fsize = os.path.getsize(file_path)
        tid = str(time.time())

        if self.status_callback:
            self.status_callback("sending", tid, fname, 0, fsize)
        try:
            resource = RNS.Resource(
                file_path,
                link,
                callback=lambda r: self._send_done(r, tid, fname)
            )
            self.active_transfers[tid] = {
                "resource": resource,
                "name": fname,
                "size": fsize,
                "type": "send"
            }
            return tid
        except Exception as e:
            if self.status_callback:
                self.status_callback("error", tid, fname, 0, fsize, str(e))
            return None

    def _send_done(self, resource, tid, fname):
        if resource.status == RNS.Resource.COMPLETE:
            if self.status_callback:
                self.status_callback("complete", tid, fname, 1, 1)
        else:
            if self.status_callback:
                self.status_callback("error", tid, fname, 0, 0)
        if tid in self.active_transfers:
            del self.active_transfers[tid]

    @staticmethod
    def receive(resource, save_dir, file_name=None):
        os.makedirs(save_dir, exist_ok=True)
        if not file_name:
            file_name = f"transfer_{int(time.time())}"
        save_path = os.path.join(save_dir, file_name)
        if resource.write_to_file(save_path):
            return save_path
        return None
