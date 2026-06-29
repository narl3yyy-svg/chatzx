import threading, RNS, json, time, os, tempfile, uuid
from contextlib import contextmanager


@contextmanager
def _null_context():
    yield
from urllib import request as urlrequest

from chatxz.utils.helpers import format_speed
from chatxz.core.discovery import (
    normalize_hash,
    message_dest_hash_for_identity,
    register_identity_from_peer,
)
from chatxz.core.lan_rns import (
    build_announce_packet,
    clear_paths_except_families,
    clear_paths_on_family,
    clear_peer_path,
    clear_peer_path_unless_family,
    detach_unhealthy_interfaces,
    ensure_serial_path_pinned,
    pin_serial_path,
    serial_path_is_pinned,
    unpin_serial_path,
    interface_family,
    interface_is_healthy,
    lan_ip_reachable,
    lan_mesh_has_peer,
    online_interfaces,
    peer_path_entry,
    peer_path_on_family,
    reinforce_serial_peer_path,
    restore_serial_path_from_announce,
    prune_bridged_lan_paths,
    prune_lan_path_for_peer,
    prune_stale_lan_paths,
    request_path_for_hash,
    request_paths_for_hash,
    scrub_peer_path,
    serial_interface_online,
    suppress_offline_lan_transports,
    udp_interface_online,
    register_udp_peer_ip,
    unicast_announce_packet,
    wait_for_peer_path,
    wait_for_peer_path_families,
)
from chatxz.utils.platform import is_android, lan_ip, physical_lan_reachable
from chatxz.core.lan_transfer import register_offer, remove_offer
from chatxz.core.audio import (
    CALL_ACCEPT,
    CALL_AUDIO,
    CALL_END,
    CALL_INVITE,
    CALL_REJECT,
    CALL_TYPES,
    OPUS_CODEC,
    STATE_ACTIVE,
    STATE_IDLE,
    STATE_INCOMING,
    STATE_OUTGOING,
    VoiceCallSession,
    parse_call_payload,
    split_call_audio_b64,
)
from chatxz.core.serial_transfer import (
    boost_serial_establishment_timeout,
    is_serial_interface,
    tune_incoming_resource,
    tune_outgoing_resource,
    tune_serial_link,
)
from chatxz.core.rns_interfaces import (
    configured_serial_enabled,
    configured_tcp_lan_enabled,
    configured_udp_lan_enabled,
    ensure_runtime_serial,
    ensure_runtime_tcp_lan_server,
    ensure_tcp_client_to_peer,
    lan_discovery_configured,
    load_settings_interfaces,
    dedupe_serial_interfaces,
    prune_dead_serial_interfaces,
    tcp_client_interface_online,
    tcp_server_interface_online,
)

APP_NAME = "chatxz"
LINK_CONNECT_TIMEOUT_S = 12
ANDROID_LINK_CONNECT_TIMEOUT_S = 14
FAILOVER_CONNECT_TIMEOUT_S = 16
LINK_CONNECT_POLL_S = 0.05
IDENTITY_WAIT_TIMEOUT_S = 12
ANDROID_IDENTITY_WAIT_TIMEOUT_S = 16
SERIAL_IDENTITY_WAIT_TIMEOUT_S = 35
SERIAL_PATH_PRIME_TIMEOUT_S = 28
SERIAL_ANNOUNCE_BURST_COUNT = 1
SERIAL_ANNOUNCE_BURST_INTERVAL_S = 0
SERIAL_CONNECT_PRIME_INTERVAL_S = 3.0
SERIAL_LINK_CONNECT_TIMEOUT_S = 22
SERIAL_INBOUND_FIRST_WAIT_S = 4
SERIAL_INBOUND_WAIT_S = 12
REVERSE_CONNECT_WAIT_S = 10
ANDROID_REVERSE_CONNECT_WAIT_S = 12
INITIATOR_INBOUND_WAIT_S = 8
ANDROID_INITIATOR_INBOUND_WAIT_S = 10
QUICK_OUTBOUND_TIMEOUT_S = 6
HTTP_WAKE_TIMEOUT_S = 1.5
LINK_FAILOVER_GRACE_S = 30
LINK_STALE_FAILOVER_IDLE_S = 90
SESSION_RECONNECT_MIN_IDLE_S = 18
DUAL_PATH_RECONNECT_MIN_IDLE_S = 4
DUAL_PATH_FAILOVER_COOLDOWN_S = 8
DUAL_PATH_DISCONNECTED_COOLDOWN_S = 4
SERIAL_SPEED_MARGIN = 1.15
PEER_LAN_UNREACHABLE_TTL_S = 90
RECEIPT_FAILOVER_TIMEOUT_S = 30
RECEIPT_FAILOVER_MIN_PENDING = 2
MAX_CONCURRENT_RECEIVES = 2
QUEUE_RETRY_INTERVAL_S = 5
QUEUE_DRAIN_DELAY_S = 1.0
QUEUE_RECEIPT_TIMEOUT_S = 30
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
MESSAGE_TYPE_LAN_HTTP = "__lan_http_offer"
MESSAGE_TYPE_TRANSFER_CANCEL = "__transfer_cancel"
LAN_HTTP_MIN_BYTES = 2 * 1024 * 1024
LAN_HTTP_CHUNK = 256 * 1024
HUB_GROUP_PEER = "__hub_group__"


def is_hub_peer_hash(peer_hash):
    clean = normalize_hash(peer_hash)
    return clean in (HUB_GROUP_PEER, "__hub_group__")


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
                 receive_dir=None, peer_resolver=None, on_queue_sent=None,
                 on_transfer_revoked=None, on_call_event=None,
                 http_port=8742, lan_transfer_enabled=False,
                 peer_endpoint_resolver=None, peer_scope_checker=None,
                 peer_transport_resolver=None, identity_serial=None,
                 dual_identity_mode=True):
        self.identity = identity
        self.identity_serial = identity_serial
        self.dual_identity_mode = bool(dual_identity_mode)
        self.config_dir = config_dir
        self.receive_dir = receive_dir or os.path.join(config_dir, "received")
        self.on_message = on_message
        self.on_file = on_file
        self.on_progress = on_progress
        self.on_link_established = on_link_established
        self.on_link_closed = on_link_closed
        self.on_queue_sent = on_queue_sent
        self.on_transfer_revoked = on_transfer_revoked
        self.on_call_event = on_call_event
        self.voice_call = VoiceCallSession()
        self.display_name = display_name
        self.auto_announce = auto_announce
        self.announce_interval = 30
        self.destination = None
        self.destination_serial = None
        self.my_dest_hash_serial = None
        self.lan_announce_interval_s = 0
        self.serial_announce_interval_s = 0
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
        self.http_port = int(http_port or 8742)
        self.lan_transfer_enabled = bool(lan_transfer_enabled)
        self.peer_endpoint_resolver = peer_endpoint_resolver
        self.peer_scope_checker = peer_scope_checker
        self.peer_transport_resolver = peer_transport_resolver
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
        self._queue_drain_timers = {}
        self._queue_drain_lock = threading.Lock()
        self.peer_links = {}
        self._session_transport = None
        self._connect_user_initiated = False
        self._connect_background = False
        self._connect_in_progress = False
        self._peer_lan_unreachable = {}
        self._user_disconnected = set()
        self._transport_reconnect_pending = False

    def _is_self_hash(self, h):
        clean = normalize_hash(h)
        if not clean:
            return False
        if self.my_dest_hash and clean == normalize_hash(self.my_dest_hash):
            return True
        if self.my_dest_hash_serial and clean == normalize_hash(self.my_dest_hash_serial):
            return True
        try:
            if self.identity and clean == normalize_hash(RNS.hexrep(self.identity.hash)):
                return True
            if self.identity_serial and clean == normalize_hash(RNS.hexrep(self.identity_serial.hash)):
                return True
        except Exception:
            pass
        return False

    def _destination_for_interface(self, iface):
        if iface and is_serial_interface(iface):
            return self.destination_serial
        return self.destination

    def ensure_serial_runtime(self):
        """Create serial identity + inbound destination when USB comes online."""
        if self.destination_serial and self.identity_serial:
            return True
        try:
            from chatxz.core.identity import IdentityManager
            mgr = IdentityManager(self.config_dir)
            mgr.load_or_create(serial_enabled=True)
            if mgr.identity_serial:
                self.identity_serial = mgr.identity_serial
        except Exception as e:
            print(f"[identity] Serial runtime setup failed: {e}")
            return False
        if not self.identity_serial:
            return False
        if not self.destination_serial:
            self.destination_serial = self._setup_inbound_destination(
                self.identity_serial, "destination_serial",
            )
            self.my_dest_hash_serial = normalize_hash(
                RNS.hexrep(self.destination_serial.hash),
            )
            print(f"[identity] Serial endpoint {self.my_dest_hash_serial[:16]}...")
        return bool(self.destination_serial)

    def _local_connect_hash_for_interface(self, iface):
        if iface and is_serial_interface(iface) and self.my_dest_hash_serial:
            return self.my_dest_hash_serial
        return self.my_dest_hash

    def _cache_link_peer(self, link, peer_hash):
        if not link or not peer_hash or peer_hash == "unknown":
            return
        canon = self.canonical_connect_hash(peer_hash, link=link)
        if canon and not self._is_self_hash(canon):
            self._link_peer_hashes[link.link_id] = canon

    @staticmethod
    def _normalize_transport(via):
        v = (via or "lan").strip().lower()
        if v in ("serial", "usb"):
            return "serial"
        return "lan"

    def _link_map_key(self, peer_hash, transport=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return ""
        if transport:
            return f"{peer}:{self._normalize_transport(transport)}"
        return peer

    @staticmethod
    def _peer_from_link_key(key):
        text = str(key or "")
        if ":" in text:
            return text.rsplit(":", 1)[0]
        return text

    def _transport_from_link(self, link):
        fam = interface_family(self._link_attached_interface(link))
        if fam == "serial":
            return "serial"
        return "lan"

    def _link_transport_matches(self, link, transport):
        if not transport:
            return True
        return self._transport_from_link(link) == self._normalize_transport(transport)

    def _peer_discovery_meta(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        if self.peer_transport_resolver:
            try:
                return self.peer_transport_resolver(peer)
            except Exception:
                return None
        return None

    def _peer_expected_transport_families(self, peer_hash):
        """Transport families allowed for a peer (serial vs LAN isolation)."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown" or is_hub_peer_hash(peer):
            return set()
        meta = self._peer_discovery_meta(peer)
        serial_ready = self._serial_transport_ready()
        if meta:
            via = (meta.get("via") or "").strip()
            ip = (meta.get("ip") or "").strip()
            if via == "serial":
                return {"serial"} if serial_ready else set()
            if ip and self._peer_lan_ip_usable(ip):
                return {"udp", "lan", "tcp"}
            if not ip and serial_ready:
                return {"serial"}
            if (
                serial_ready
                and ip
                and not self._peer_lan_ip_usable(ip)
                and self._peer_has_path_on_family(peer, "serial")
            ):
                return {"serial"}
        if serial_ready and self._lan_transport_ready():
            if self._peer_has_path_on_family(peer, "serial"):
                if not self._peer_has_path_on_family(peer, "udp") and not self._peer_has_path_on_family(peer, "tcp"):
                    return {"serial"}
                meta = meta or self._peer_discovery_meta(peer)
                if meta and (meta.get("via") or "").strip() == "serial":
                    return {"serial"}
                if meta and not (meta.get("ip") or "").strip():
                    return {"serial"}
        if (
            serial_ready
            and self._peer_has_path_on_family(peer, "serial")
            and not self._peer_has_path_on_family(peer, "udp")
            and not self._peer_has_path_on_family(peer, "tcp")
        ):
            return {"serial"}
        if meta and (meta.get("via") or "").strip() == "serial" and serial_ready:
            return {"serial"}
        if serial_ready and self._peer_has_path_on_family(peer, "serial"):
            meta = meta or self._peer_discovery_meta(peer) or {}
            ip = (meta.get("ip") or "").strip()
            via = (meta.get("via") or "").strip()
            if via == "serial" or not ip or not self._peer_lan_ip_usable(ip):
                return {"serial"}
        if self._peer_has_path_on_family(peer, "udp") or self._peer_has_path_on_family(peer, "tcp"):
            return {"udp", "lan", "tcp"}
        return set()

    def _link_remote_peer_hash(self, link):
        """Resolved destination hash for a link's remote party (authoritative when known)."""
        if not link:
            return ""
        identity_peer = self._peer_hash_from_link_identity(link)
        if identity_peer and identity_peer != "unknown" and not self._is_self_hash(identity_peer):
            return self.dest_hash_for(identity_peer)
        cached = self._link_peer_hashes.get(getattr(link, "link_id", None))
        if cached and cached != "unknown" and not self._is_self_hash(cached):
            return self.dest_hash_for(cached)
        return ""

    def _link_matches_peer(self, link, peer_hash):
        if not link:
            return False
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return False
        remote = self._link_remote_peer_hash(link)
        if remote:
            return self.hashes_equivalent(remote, peer)
        if self.peer_links.get(peer) is link:
            return True
        for cached_peer, cached_link in self.peer_links.items():
            if cached_link is link and self.hashes_equivalent(cached_peer, peer):
                return True
        return False

    def _link_acceptable_for_peer(self, link, peer_hash):
        if not link:
            return False
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            if is_serial_interface(self._link_attached_interface(link)):
                return True
            return False
        expected = self._peer_expected_transport_families(peer)
        if not expected:
            return True
        fam = interface_family(self._link_attached_interface(link))
        if fam == "serial":
            return "serial" in expected
        if fam in ("udp", "lan", "tcp"):
            return bool(expected & {"udp", "lan", "tcp"})
        return fam in expected

    def _peer_path_interface_for_peer(self, peer_hash):
        """Return path interface only when it matches the peer's transport zone."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        scrub_peer_path(peer)
        _, path_iface = peer_path_entry(peer)
        if not path_iface or not interface_is_healthy(path_iface):
            return None
        expected = self._peer_expected_transport_families(peer)
        if not expected:
            return path_iface
        fam = interface_family(path_iface)
        if fam == "serial":
            return path_iface if "serial" in expected else None
        if fam in ("udp", "lan", "tcp"):
            return path_iface if expected & {"udp", "lan", "tcp"} else None
        return path_iface if fam in expected else None

    def _parallel_sessions_allowed(self):
        """True when USB serial and LAN are both up — independent peer links per transport."""
        try:
            from chatxz.core.transport_isolation import dual_transport_isolation_enabled
            return dual_transport_isolation_enabled()
        except Exception:
            return False

    def _teardown_other_peer_links(self, keep_peer_hash, handoff=False):
        """Close active links to every peer except the one being connected."""
        if self._parallel_sessions_allowed():
            return 0
        keep = self.dest_hash_for(keep_peer_hash)
        if not keep or keep == "unknown":
            return 0
        closed = 0
        for link in list(self.links.values()):
            remote = self.dest_hash_for(self._peer_for_link(link))
            if not remote or remote == "unknown" or self.hashes_equivalent(remote, keep):
                continue
            try:
                if handoff:
                    self._link_handoff = True
                link.teardown()
                closed += 1
            except Exception:
                pass
            finally:
                if handoff:
                    self._link_handoff = False
        if closed:
            print(f"[connect] Closed {closed} link(s) to other peer(s)")
        return closed

    def _peer_allowed_by_scope(self, peer_hash, link=None):
        if not peer_hash or peer_hash == "unknown":
            if link and is_serial_interface(self._link_attached_interface(link)):
                return True
            return not self.peer_scope_checker
        if link:
            iface = self._link_attached_interface(link)
            if is_serial_interface(iface):
                return True
            if iface and not self._link_acceptable_for_peer(link, peer_hash):
                return False
        if not self.peer_scope_checker:
            return True
        if is_hub_peer_hash(peer_hash):
            return True
        try:
            return bool(self.peer_scope_checker(peer_hash, link=link))
        except TypeError:
            try:
                return bool(self.peer_scope_checker(peer_hash))
            except Exception:
                return True
        except Exception:
            return True

    def _link_for_peer(self, peer_hash, transport=None):
        raw = str(peer_hash or "")
        if ":" in raw and not transport:
            base, suffix = raw.rsplit(":", 1)
            peer = self.dest_hash_for(base)
            transport = suffix
        else:
            peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        if transport:
            key = self._link_map_key(peer, transport)
            link = self.peer_links.get(key)
            if link and self._link_matches_peer(link, peer):
                return link
            return None
        link = self.peer_links.get(peer)
        if link and self._link_matches_peer(link, peer):
            return link
        for cached_key, cached_link in self.peer_links.items():
            if not self.hashes_equivalent(self._peer_from_link_key(cached_key), peer):
                continue
            if self._link_matches_peer(cached_link, peer):
                return cached_link
        for link_id, cached in self._link_peer_hashes.items():
            if self.hashes_equivalent(cached, peer):
                link = self.links.get(link_id)
                if link and self._link_matches_peer(link, peer):
                    return link
        return None

    def _register_peer_link(self, link, peer_hash, transport=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown" or not link:
            return
        remote = self._link_remote_peer_hash(link)
        if remote and not self.hashes_equivalent(remote, peer):
            print(
                f"[messaging] Rejected link map {peer[:16]}... "
                f"(remote identity {remote[:16]}...)"
            )
            return
        t = transport or self._transport_from_link(link)
        key = self._link_map_key(peer, t)
        self.peer_links[key] = link
        if key != peer:
            self.peer_links.pop(peer, None)
        self._cache_link_peer(link, peer)

    def _unlink_peer(self, peer_hash, transport=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer:
            return
        if transport:
            self.peer_links.pop(self._link_map_key(peer, transport), None)
            return
        self.peer_links.pop(peer, None)
        for key in list(self.peer_links.keys()):
            if self.hashes_equivalent(self._peer_from_link_key(key), peer):
                self.peer_links.pop(key, None)

    def _other_active_links_for_peer(self, peer_hash, except_link=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return []
        matches = []
        for link_id, cached in list(self._link_peer_hashes.items()):
            if not self.hashes_equivalent(cached, peer):
                continue
            link = self.links.get(link_id)
            if not link or (except_link and link.link_id == except_link.link_id):
                continue
            try:
                if link.status == RNS.Link.ACTIVE:
                    matches.append(link)
            except Exception:
                matches.append(link)
        return matches

    def _adopt_healthy_peer_link(self, peer_hash, promote_session=None):
        """Promote a healthy background link for one peer (optionally the UI session)."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        session_peer = self.dest_hash_for(
            self._session_peer_hash or self.active_peer_hash or ""
        )
        if promote_session is None:
            promote_session = bool(
                session_peer and self.hashes_equivalent(peer, session_peer)
            )
        if self.active_link and self._peer_link_active(peer):
            if self._link_interface_healthy(self.active_link):
                if self.hashes_equivalent(
                    self._peer_for_link(self.active_link), peer
                ):
                    return self.active_link
        for link in self._other_active_links_for_peer(peer):
            if not self._link_interface_healthy(link):
                continue
            if not self._link_acceptable_for_peer(link, peer):
                continue
            if not self._link_matches_peer(link, peer):
                continue
            self._register_peer_link(link, peer)
            if promote_session:
                self._notify_link_established(
                    link, peer, promote_active=True, background=False,
                )
            return link
        return None

    def _teardown_stale_peer_links(self, peer_hash, handoff=False):
        """Close dead or wrong-transport links to one peer before reconnect."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return 0
        closed = 0
        for link_id, link in list(self.links.items()):
            cached = self._link_peer_hashes.get(link_id)
            if not cached or not self.hashes_equivalent(cached, peer):
                continue
            try:
                if link.status == RNS.Link.ACTIVE and self._link_interface_healthy(link):
                    continue
            except Exception:
                pass
            try:
                if handoff:
                    self._link_handoff = True
                link.teardown()
                closed += 1
            except Exception:
                pass
            finally:
                if handoff:
                    self._link_handoff = False
        return closed

    def _consolidate_peer_links(self, peer_hash, keep_link=None, transport=None):
        """Keep one active link per peer per transport — tear down duplicate sessions."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return 0
        keep_id = getattr(keep_link, "link_id", None) if keep_link else None
        keep_fam = interface_family(self._link_attached_interface(keep_link)) if keep_link else None
        parallel = self._parallel_sessions_allowed()
        closed = 0
        for link in list(self._links_for_peer(peer)):
            if keep_id and link.link_id == keep_id:
                continue
            if parallel and keep_fam:
                fam = interface_family(self._link_attached_interface(link))
                if fam != keep_fam:
                    continue
            if transport and not self._link_transport_matches(link, transport):
                continue
            try:
                if getattr(link, "status", None) == RNS.Link.CLOSED:
                    continue
            except Exception:
                pass
            try:
                link.teardown()
                closed += 1
            except Exception:
                pass
        if closed:
            print(f"[messaging] Closed {closed} duplicate link(s) for {peer[:16]}...")
        return closed

    def _finish_connect(self, peer_hash, link=None, user_initiated=None, transport=None):
        """After a successful connect: one link per peer per transport and drain queue."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return True
        initiated = (
            bool(user_initiated)
            if user_initiated is not None
            else bool(getattr(self, "_connect_user_initiated", False))
        )
        use_link = link
        if not use_link:
            use_link = (
                self._adopt_healthy_peer_link(peer)
                or self._best_outgoing_link(peer)
            )
        if use_link:
            self._consolidate_peer_links(
                peer, keep_link=use_link, transport=transport,
            )
        if not self.is_user_disconnected(peer):
            self._schedule_queue_drain(peer, link=use_link, include_files=True)
            if initiated:
                self._schedule_hub_queue_drain()
        return True

    def _discovery_peer_meta(self, dest_hex, peer_ip=None, peer_lookup=None):
        if not peer_lookup:
            return None
        try:
            return peer_lookup(peer_ip, dest_hex)
        except TypeError:
            try:
                return peer_lookup(dest_hex)
            except Exception:
                return None
        except Exception:
            return None

    def _should_prefer_serial_connect(self, dest_hex, peer_ip=None, peer_lookup=None):
        """True when the peer is a direct USB neighbor (no usable in-scope LAN IP)."""
        if not self._serial_transport_ready():
            return False
        meta = self._discovery_peer_meta(dest_hex, peer_ip=peer_ip, peer_lookup=peer_lookup)
        if meta:
            if (meta.get("via") or "").strip() == "serial":
                return True
            if not (meta.get("ip") or "").strip():
                return True
        if peer_ip and self._peer_lan_ip_usable(peer_ip):
            return False
        if not peer_ip:
            return True
        return False

    def _udp_connect_ready(self, dest_hex, peer_ip=None, peer_lan_down=False, prefer_serial=False):
        if prefer_serial or peer_lan_down or not physical_lan_reachable() or not self._lan_transport_ready():
            return False
        if not configured_udp_lan_enabled(load_settings_interfaces(self.config_dir)):
            return False
        if peer_ip:
            return self._peer_lan_ip_usable(peer_ip)
        return self._peer_has_path_on_family(dest_hex, "udp")

    def _tcp_connect_ready(self, dest_hex, peer_ip=None, peer_lan_down=False, prefer_serial=False):
        if prefer_serial or peer_lan_down or not physical_lan_reachable() or not self._lan_transport_ready():
            return False
        if not configured_tcp_lan_enabled(load_settings_interfaces(self.config_dir)):
            return False
        if peer_ip:
            return self._peer_lan_ip_usable(peer_ip)
        return self._peer_has_path_on_family(dest_hex, "tcp")

    def linked_peers(self):
        out = []
        for key, link in list(self.peer_links.items()):
            try:
                if getattr(link, "status", None) == RNS.Link.CLOSED:
                    continue
            except Exception:
                pass
            peer = self._peer_from_link_key(key)
            if ":" in str(key):
                out.append(str(key))
            else:
                out.append(f"{peer}:{self._transport_from_link(link)}")
        return out

    def _hub_tcp_linked_peers(self):
        """Peers on hub TCP transport only (not TCP LAN P2P dials)."""
        role, _ = self._load_hub_settings()
        if role == "off":
            return []
        hub_host, hub_port = self._hub_endpoint_from_settings()
        out = []
        seen = set()
        for key, link in list(self.peer_links.items()):
            peer = self._peer_from_link_key(key)
            if not peer or is_hub_peer_hash(peer) or peer in seen:
                continue
            if not link:
                continue
            if self._link_is_hub_transport(
                self._link_attached_interface(link),
                role=role,
                hub_host=hub_host,
                hub_port=hub_port,
            ):
                seen.add(peer)
                out.append(peer)
        return out

    def _hub_message_acceptable(self, chat_msg, link):
        if not getattr(chat_msg, "hub_group", False):
            return True
        role, _ = self._load_hub_settings()
        if role == "off":
            return False
        hub_host, hub_port = self._hub_endpoint_from_settings()
        return self._link_is_hub_transport(
            self._link_attached_interface(link),
            role=role,
            hub_host=hub_host,
            hub_port=hub_port,
        )

    def disconnect_peer(self, peer_hash, user_initiated=False, transport=None):
        peer = self.dest_hash_for(peer_hash)
        transport = self._normalize_transport(transport) if transport else None
        if user_initiated and peer and not transport:
            self.mark_user_disconnected(peer)
            self.clear_session_peer()
            self._transport_reconnect_pending = False
            self._last_link_lost_at = 0
        closed = 0
        for link in list(self.links.values()):
            resolved = self._peer_hash_from_link_identity(link)
            if not resolved:
                cached = self._link_peer_hashes.get(link.link_id)
                resolved = self.dest_hash_for(cached) if cached else ""
            if peer and resolved and not self.hashes_equivalent(resolved, peer):
                continue
            if peer and not resolved:
                continue
            if transport and not self._link_transport_matches(link, transport):
                continue
            try:
                link.teardown()
                closed += 1
            except Exception:
                pass
        if peer:
            self._unlink_peer(peer, transport=transport)
        if user_initiated and peer:
            active_matches = (
                self.active_link
                and (
                    not transport
                    or self._link_transport_matches(self.active_link, transport)
                )
            )
            if active_matches and self.active_peer_hash and self.hashes_equivalent(
                self.active_peer_hash, peer
            ):
                self.active_link = None
                self.active_peer_hash = None
                self._send_link = None
            if not transport:
                self.clear_session_peer()
                self._transport_reconnect_pending = False
                self._last_link_lost_at = 0
                self.mark_user_disconnected(peer)
            elif not self._other_active_links_for_peer(peer):
                self.clear_session_peer()
                self._transport_reconnect_pending = False
                self._last_link_lost_at = 0
                self.mark_user_disconnected(peer)
        return closed > 0

    def mark_user_disconnected(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if peer and peer != "unknown":
            self._user_disconnected.add(peer)

    def clear_user_disconnected(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer:
            return
        self._user_disconnected = {
            h for h in self._user_disconnected
            if not self.hashes_equivalent(h, peer)
        }

    def is_user_disconnected(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer:
            return False
        return any(
            self.hashes_equivalent(peer, blocked)
            for blocked in self._user_disconnected
        )

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
        identity_peer = self._peer_hash_from_link_identity(link)
        if identity_peer and identity_peer != "unknown" and not self._is_self_hash(identity_peer):
            self._cache_link_peer(link, identity_peer)
            return identity_peer
        cached = self._link_peer_hashes.get(link.link_id) if link else None
        if cached and not self._is_self_hash(cached):
            canon = self.canonical_connect_hash(cached, link=link)
            if canon:
                return canon
        resolved = self._resolve_remote_peer(link, fallback=fallback)
        if resolved and resolved != "unknown" and not self._is_self_hash(resolved):
            resolved = self.canonical_connect_hash(resolved, link=link)
            if resolved:
                self._cache_link_peer(link, resolved)
                return resolved
        if fallback and not self._is_self_hash(fallback):
            mapped = self.canonical_connect_hash(fallback, link=link)
            if mapped:
                self._cache_link_peer(link, mapped)
                return mapped
        if cached and not self._is_self_hash(cached):
            canon = self.canonical_connect_hash(cached, link=link)
            if canon:
                return canon
        return "unknown"

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

    def canonical_connect_hash(self, any_hash, link=None):
        """Resolve identity or alias hashes to the message destination (connect) hash."""
        clean = normalize_hash(any_hash)
        if not clean or clean == "unknown" or self._is_self_hash(clean):
            if link:
                from_link = self._peer_hash_from_link_identity(link)
                if from_link and from_link != "unknown" and not self._is_self_hash(from_link):
                    return from_link
            return ""
        mapped = self.dest_hash_for(clean)
        if mapped in self.dest_to_identity:
            return mapped
        ident = self._identity_for_hash(clean)
        if ident:
            dest = self._dest_hash_from_identity(ident)
            if dest and not self._is_self_hash(dest):
                return dest
        if link:
            from_link = self._peer_hash_from_link_identity(link)
            if from_link and from_link != "unknown" and not self._is_self_hash(from_link):
                return from_link
        if mapped and len(mapped) == 32:
            return mapped
        return ""

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
        iface = getattr(link, "attached_interface", None)
        if iface:
            return iface
        for attr in ("interface", "parent_interface"):
            iface = getattr(link, attr, None)
            if iface:
                return iface
        return None

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
        if fam == "tcp":
            return 95
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

    def _load_hub_settings(self):
        try:
            import json
            import os
            from chatxz.utils.helpers import get_config_dir
            path = os.path.join(self.config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
            return (
                settings.get("hub_role") or "off",
                (settings.get("hub_server_hash") or "").strip(),
            )
        except Exception:
            return "off", ""

    def _hub_endpoint_from_settings(self):
        try:
            import json
            import os
            from chatxz.utils.helpers import get_config_dir
            path = os.path.join(self.config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
            return (
                (settings.get("hub_host") or "").strip(),
                int(settings.get("hub_port") or 4242),
            )
        except Exception:
            return "", 4242

    def _link_is_hub_transport(self, iface, role=None, hub_host=None, hub_port=None):
        if iface is None or interface_family(iface) != "tcp":
            return False
        if role is None:
            role, _ = self._load_hub_settings()
        if role == "off":
            return False
        if hub_host is None or hub_port is None:
            hub_host, hub_port = self._hub_endpoint_from_settings()
        if role == "client":
            target = (getattr(iface, "target_host", None) or "").strip()
            port = int(
                getattr(iface, "target_port", None)
                or getattr(iface, "port", None)
                or 4242
            )
            return bool(hub_host) and target == hub_host and port == hub_port
        if role == "server":
            return type(iface).__name__ == "TCPServerInterface"
        return False

    def _peer_uses_hub_transport(self, peer_hash):
        """Hub TCP is for group chat and the hub server — not local LAN P2P."""
        if is_hub_peer_hash(peer_hash):
            return True
        role, hub_server_hash = self._load_hub_settings()
        if role == "off":
            return False
        peer = normalize_hash(self.dest_hash_for(peer_hash) or peer_hash or "")
        if len(peer) != 32:
            return False
        hub_hex = normalize_hash(hub_server_hash or "")
        if hub_hex and self.hashes_equivalent(peer, hub_hex):
            return True
        return False

    def _hub_transport_active(self):
        role, _ = self._load_hub_settings()
        return role != "off"

    def _has_online_family(self, family):
        if family == "tcp":
            if (
                configured_tcp_lan_enabled(load_settings_interfaces(self.config_dir))
                and not self._hub_transport_active()
            ):
                return (
                    tcp_server_interface_online() is not None
                    or tcp_client_interface_online() is not None
                )
            return (
                tcp_client_interface_online() is not None
                or tcp_server_interface_online() is not None
            )
        if family == "serial":
            return serial_interface_online() is not None
        if family == "udp":
            if not lan_discovery_configured(load_settings_interfaces(self.config_dir)):
                return False
            if not bool(online_interfaces(family="udp")):
                return False
            if is_android():
                return True
            return physical_lan_reachable() or lan_mesh_has_peer()
        if family == "lan":
            return lan_mesh_has_peer()
        return bool(online_interfaces(family=family))

    def _dual_path_configured(self):
        interfaces = load_settings_interfaces(self.config_dir)
        return configured_serial_enabled(interfaces) and lan_discovery_configured(interfaces)

    def _session_reconnect_min_idle(self):
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        if peer and self._peer_expected_transport_families(peer) == {"serial"}:
            return SESSION_RECONNECT_MIN_IDLE_S
        if self._dual_path_configured():
            return DUAL_PATH_RECONNECT_MIN_IDLE_S
        return SESSION_RECONNECT_MIN_IDLE_S

    def _failover_cooldown(self):
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        serial_only = bool(peer and self._peer_expected_transport_families(peer) == {"serial"})
        disconnected = (
            not self.active_link
            and bool(self.dest_hash_for(self._session_peer_hash or ""))
        )
        if serial_only:
            return self._failover_cooldown_s
        if self._dual_path_configured():
            if disconnected:
                return DUAL_PATH_DISCONNECTED_COOLDOWN_S
            return DUAL_PATH_FAILOVER_COOLDOWN_S
        if disconnected:
            return DUAL_PATH_RECONNECT_MIN_IDLE_S
        return self._failover_cooldown_s

    def _link_rtt_seconds(self, link):
        if not link:
            return None
        rtt = getattr(link, "rtt", None)
        if rtt is None:
            return None
        try:
            return float(rtt)
        except Exception:
            return None

    def _serial_faster_than_lan(self, peer):
        """True when serial is confirmed up and measurably faster than LAN/UDP."""
        if not self._serial_transport_ready():
            return False
        if not physical_lan_reachable() or not self._has_online_family("udp"):
            return True
        if not self._peer_has_path_on_family(peer, "serial"):
            return False
        serial_rtt = None
        for link in self._links_for_peer(peer):
            if interface_family(self._link_attached_interface(link)) == "serial":
                serial_rtt = self._link_rtt_seconds(link)
                if serial_rtt is not None:
                    break
        if serial_rtt is None and self.active_link:
            if interface_family(self._link_attached_interface(self.active_link)) == "serial":
                serial_rtt = self._link_rtt_seconds(self.active_link)
        if serial_rtt is None:
            return False
        lan_rtt = None
        lan_fams = ("udp", "lan", "tcp")
        for link in self._links_for_peer(peer):
            fam = interface_family(self._link_attached_interface(link))
            if fam in lan_fams:
                lan_rtt = self._link_rtt_seconds(link)
                if lan_rtt is not None:
                    break
        if lan_rtt is None and self.active_link:
            fam = interface_family(self._link_attached_interface(self.active_link))
            if fam in lan_fams:
                lan_rtt = self._link_rtt_seconds(self.active_link)
        if lan_rtt is None:
            return False
        return serial_rtt * SERIAL_SPEED_MARGIN < lan_rtt

    def _failover_families_to_try(self, peer, peer_ip=None):
        """Ordered transports to attempt when reconnecting (LAN preferred unless serial is faster)."""
        raw_session = (self._session_transport or "").strip().lower()
        session_transport = self._normalize_transport(raw_session) if raw_session else None
        if session_transport == "serial" and self._serial_transport_ready():
            return ["serial"]
        if session_transport == "lan":
            interfaces = load_settings_interfaces(self.config_dir)
            udp_lan = configured_udp_lan_enabled(interfaces)
            tcp_lan = configured_tcp_lan_enabled(interfaces)
            if tcp_lan and not udp_lan:
                return ["tcp"]
            if udp_lan:
                return ["udp", "tcp"] if tcp_lan else ["udp"]
            return ["udp", "tcp", "lan"]
        if self._hub_transport_active() and self._peer_uses_hub_transport(peer):
            return ["tcp"]
        meta = self._peer_discovery_meta(peer)
        if meta and (meta.get("via") or "").strip() == "serial":
            if self._serial_transport_ready():
                return ["serial"]
            return []
        interfaces = load_settings_interfaces(self.config_dir)
        udp_lan = configured_udp_lan_enabled(interfaces)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        peer_lan_down = bool(peer_ip and self._peer_lan_recently_unreachable(peer_ip))
        lan_up = physical_lan_reachable() and not peer_lan_down and (
            (udp_lan and self._has_online_family("udp"))
            or (tcp_lan and self._has_online_family("tcp"))
        )
        serial_up = self._serial_transport_ready()
        if tcp_lan and not udp_lan:
            if lan_up and serial_up:
                order = (
                    ("serial", "tcp") if self._serial_faster_than_lan(peer) else ("tcp", "serial")
                )
            elif lan_up:
                order = ("tcp", "serial")
            elif serial_up:
                order = ("serial", "tcp")
            else:
                order = ("tcp", "serial")
        elif lan_up and serial_up:
            if self._serial_faster_than_lan(peer):
                order = ("serial", "udp", "tcp", "lan") if tcp_lan else ("serial", "udp", "lan")
            else:
                order = ("udp", "tcp", "lan", "serial") if tcp_lan else ("udp", "lan", "serial")
        elif lan_up:
            order = ("udp", "tcp", "lan", "serial") if tcp_lan else ("udp", "lan", "serial")
        elif serial_up:
            order = ("serial", "udp", "tcp", "lan") if tcp_lan else ("serial", "udp", "lan")
        else:
            order = ("udp", "tcp", "serial", "lan") if tcp_lan else ("udp", "serial", "lan")
        expected = self._peer_expected_transport_families(peer)
        seen = set()
        out = []
        for fam in order:
            if not fam or fam in seen:
                continue
            if expected:
                if fam == "serial" and "serial" not in expected:
                    continue
                if fam in ("udp", "lan", "tcp") and not (expected & {"udp", "lan", "tcp"}):
                    continue
            seen.add(fam)
            out.append(fam)
        if expected == {"serial"} and "serial" in seen:
            return ["serial"]
        return out

    def _failover_announce(self, prefer_family, peer_ip=None):
        """Refresh RNS path on the target transport before failover reconnect."""
        if prefer_family == "tcp":
            if peer_ip:
                ensure_tcp_client_to_peer(peer_ip, config_dir=self.config_dir)
            self._silent_announce(peer_ip=peer_ip)
            return
        if prefer_family == "serial":
            if self._serial_transport_ready():
                self._burst_serial_announce(count=1, force=True)
            return
        if prefer_family in ("udp", "lan"):
            if physical_lan_reachable():
                self._silent_announce(peer_ip=peer_ip, also_serial=False)
            elif self._serial_transport_ready():
                self._burst_serial_announce(count=1, force=True)
            return
        self._silent_announce(peer_ip=peer_ip if physical_lan_reachable() else None)

    def _preferred_failover_family(self, peer, attached=None, peer_ip=None):
        if self._hub_transport_active() and self._peer_uses_hub_transport(peer):
            return "tcp"
        attached = attached or self._link_attached_interface(self.active_link)
        att_fam = interface_family(attached)
        serial_up = self._serial_transport_ready()
        physical_lan = physical_lan_reachable()
        interfaces = load_settings_interfaces(self.config_dir)
        udp_lan = configured_udp_lan_enabled(interfaces)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        udp_up = self._has_online_family("udp") if udp_lan else False
        tcp_up = self._has_online_family("tcp") if tcp_lan else False
        peer_lan_down = bool(peer_ip and self._peer_lan_recently_unreachable(peer_ip))
        path_iface = self._peer_path_interface(peer)
        path_fam = interface_family(path_iface) if path_iface else ""
        if peer_lan_down and serial_up:
            return "serial"
        if path_fam == "serial" and serial_up and not physical_lan:
            return "serial"
        if physical_lan and tcp_lan and not udp_lan and tcp_up and not peer_lan_down:
            if att_fam == "serial" and serial_up:
                if self._serial_faster_than_lan(peer) and self._peer_has_path_on_family(peer, "serial"):
                    return "serial"
                return "tcp"
            return "tcp"
        # LAN primary whenever physical ethernet/Wi-Fi is up and peer answers on LAN.
        if physical_lan and (udp_up or tcp_up) and not peer_lan_down:
            prefer = "tcp" if (tcp_lan and tcp_up and not udp_lan) else "udp"
            if att_fam == "serial" and serial_up:
                if self._serial_faster_than_lan(peer) and self._peer_has_path_on_family(peer, "serial"):
                    return "serial"
                return prefer
            if att_fam == "serial" and not serial_up:
                return prefer
            if tcp_lan and tcp_up and udp_lan and udp_up:
                return "tcp"
            return prefer
        if physical_lan and lan_mesh_has_peer() and att_fam == "serial":
            return "lan"
        if serial_up and not physical_lan:
            return "serial"
        if att_fam in ("udp", "lan", "tcp") and not physical_lan and serial_up:
            return "serial"
        if att_fam == "serial":
            if tcp_up:
                return "tcp"
            if udp_up:
                return "udp"
            if self._has_online_family("lan"):
                return "lan"
        if att_fam == "lan" and not lan_mesh_has_peer():
            if bool(online_interfaces(family="udp")):
                return "udp"
            if serial_up:
                return "serial"
        if att_fam == "udp" and not physical_lan and serial_up:
            return "serial"
        if att_fam == "udp" and lan_mesh_has_peer():
            return "lan"
        path_iface = self._peer_path_interface(peer)
        if path_iface and self._interface_healthy(path_iface):
            fam = interface_family(path_iface)
            if fam != att_fam:
                return fam
        if self._has_online_family("udp"):
            return "udp"
        if self._has_online_family("lan"):
            return "lan"
        if self._has_online_family("serial"):
            return "serial"
        return None

    def _prepare_failover_path(self, peer, prefer_family=None, peer_ip=None, peer_port=None):
        if self._interrupted():
            return False
        self._ensure_runtime_serial_transport()
        if peer_ip and self._peer_lan_recently_unreachable(peer_ip):
            peer_ip = None
            if prefer_family in ("udp", "lan"):
                prefer_family = "serial" if self._has_online_family("serial") else prefer_family
            clear_paths_on_family("udp")
        suppress_offline_lan_transports()
        dedupe_serial_interfaces()
        prune_dead_serial_interfaces()
        if not self._serial_transport_ready():
            serial_cleared = clear_paths_on_family("serial")
            if serial_cleared:
                print(f"[connect] Cleared {serial_cleared} stale serial path(s)")
        pruned = prune_stale_lan_paths()
        if pruned:
            print(f"[connect] Cleared {pruned} stale LAN path(s)")
        bridged = prune_bridged_lan_paths()
        if bridged:
            print(f"[connect] Cleared {bridged} bridged LAN path(s)")
        if prefer_family == "serial":
            keep_families = ("serial",)
        elif prefer_family == "tcp":
            keep_families = ("tcp",)
        elif prefer_family in ("lan", "udp"):
            keep_families = ("udp", "lan")
        else:
            keep_families = None
        if keep_families:
            cleared = clear_paths_except_families(keep_families)
            if cleared:
                print(f"[connect] Cleared {cleared} path(s) off {prefer_family} transport")
        detached = detach_unhealthy_interfaces()
        if detached:
            print(f"[connect] Detached {detached} offline RNS interface(s)")
        stop = self._interrupted
        physical_lan = physical_lan_reachable()
        self._failover_announce(prefer_family, peer_ip=peer_ip)
        if prefer_family == "serial":
            if not self._serial_transport_ready():
                print("[connect] Serial interface offline — skipping serial path prep")
                clear_paths_on_family("serial")
                return False
            prune_lan_path_for_peer(peer)
            clear_peer_path_unless_family(peer, "serial")
            restored = restore_serial_path_from_announce(peer)
            if not restored:
                reinforce_serial_peer_path(peer)
            path_iface = restored or wait_for_peer_path_families(
                peer, families=("serial",), timeout_s=18.0, should_stop=stop,
            )
            if not path_iface:
                self._prime_serial_path(peer, timeout_s=SERIAL_PATH_PRIME_TIMEOUT_S)
                path_iface = wait_for_peer_path_families(
                    peer, families=("serial",), timeout_s=10.0, should_stop=stop,
                )
        elif prefer_family in ("lan", "udp") and self._lan_transport_ready():
            if peer_ip and physical_lan:
                register_udp_peer_ip(peer_ip)
                self._wake_peer(
                    peer_ip, peer_port or 8742, self.my_dest_hash or "",
                )
            elif peer_ip and not physical_lan:
                peer_ip = None
            request_paths_for_hash(peer, family="udp")
            families = ("udp", "lan") if prefer_family == "lan" else (prefer_family,)
            path_iface = wait_for_peer_path_families(
                peer, families=families, timeout_s=14.0, should_stop=stop,
            )
            if not path_iface:
                self._prime_udp_path(peer, peer_ip=peer_ip, timeout_s=6.0)
                path_iface = wait_for_peer_path_families(
                    peer, families=families, timeout_s=8.0, should_stop=stop,
                )
        elif prefer_family == "tcp":
            if peer_ip and physical_lan:
                register_udp_peer_ip(peer_ip)
                self._wake_peer(
                    peer_ip, peer_port or 8742, self.my_dest_hash or "",
                )
            if peer_ip:
                ensure_tcp_client_to_peer(peer_ip, config_dir=self.config_dir)
            request_paths_for_hash(peer, family="tcp")
            path_iface = wait_for_peer_path_families(
                peer, families=("tcp",), timeout_s=14.0, should_stop=stop,
            )
            if not path_iface:
                self._prime_tcp_path(peer, peer_ip=peer_ip, timeout_s=6.0)
                path_iface = wait_for_peer_path_families(
                    peer, families=("tcp",), timeout_s=8.0, should_stop=stop,
                )
        else:
            request_paths_for_hash(peer, family=prefer_family)
            families = (prefer_family,) if prefer_family else (None,)
            wait_s = 12.0 if prefer_family in ("lan", "udp", None) else 18.0
            path_iface = wait_for_peer_path_families(
                peer, families=families, timeout_s=wait_s, should_stop=stop,
            )
        if path_iface:
            fam = interface_family(path_iface)
            print(f"[connect] Path ready on {type(path_iface).__name__} ({fam or prefer_family})")
            return True
        print(f"[connect] Waiting for path to {peer[:16]}... (no {prefer_family or 'usable'} path yet)")
        return False

    def link_needs_failover(self):
        if self.dual_identity_mode:
            return False, ""
        if not self.active_link or not self.active_peer_hash:
            return False, ""
        if self._has_active_transfer():
            return False, ""
        peer = self.dest_hash_for(self.active_peer_hash)
        if not peer or peer == "unknown":
            return False, ""

        attached = self._link_attached_interface(self.active_link)
        if self._hub_transport_active() and self._peer_uses_hub_transport(peer):
            att_fam = interface_family(attached)
            if att_fam == "tcp" and self._link_interface_healthy(self.active_link):
                return False, ""
            if self._has_online_family("tcp") and not self._link_interface_healthy(self.active_link):
                return True, "hub TCP link offline"
            if att_fam != "tcp" and self._has_online_family("tcp"):
                return True, "hub path on TCP"
            return False, ""
        in_grace = (time.time() - self._last_link_established_at) < LINK_FAILOVER_GRACE_S

        if not self._link_interface_healthy(self.active_link):
            return True, f"link interface offline ({type(attached).__name__ if attached else 'none'})"

        path_iface = self._peer_path_interface_for_peer(peer)
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

        if att_fam == "udp" and not self._lan_transport_ready():
            if self._has_online_family("serial"):
                return True, "LAN down, serial available"
            if lan_mesh_has_peer():
                return True, "UDP down, AutoInterface available"

        if att_fam == "udp" and not physical_lan_reachable() and self._has_online_family("serial"):
            if not in_grace:
                return True, "ethernet down, serial available"

        serial_only = self._peer_expected_transport_families(peer) == {"serial"}
        lan_only = bool(
            self._peer_expected_transport_families(peer)
            and "serial" not in self._peer_expected_transport_families(peer)
        )
        parallel = self._parallel_sessions_allowed()

        expected = self._peer_expected_transport_families(peer)
        if parallel and expected:
            if not self._link_interface_healthy(self.active_link):
                return True, f"link interface offline ({type(attached).__name__ if attached else 'none'})"
            if serial_only and att_fam == "serial":
                return False, ""
            if lan_only and att_fam in ("udp", "lan", "tcp"):
                return False, ""
            if serial_only and att_fam in ("udp", "lan", "tcp") and not in_grace:
                return True, "serial peer requires serial transport"
            if lan_only and att_fam == "serial" and not in_grace:
                return True, "LAN peer requires LAN transport"

        if serial_only and att_fam == "serial" and self._link_interface_healthy(self.active_link):
            return False, ""

        if (
            not parallel
            and att_fam in ("udp", "lan")
            and self._has_online_family("serial")
            and self._peer_has_path_on_family(peer, "serial")
            and not in_grace
            and not serial_only
        ):
            return True, "peer path on serial"

        if att_fam == "serial" and not self._serial_transport_ready():
            if (self._has_online_family("udp") or self._has_online_family("lan")) and physical_lan_reachable():
                if not serial_only:
                    return True, "serial offline, LAN available"

        if (
            not parallel
            and att_fam == "serial"
            and physical_lan_reachable()
            and self._has_online_family("udp")
            and not in_grace
            and not serial_only
        ):
            if self._serial_faster_than_lan(peer) and self._peer_has_path_on_family(peer, "serial"):
                return False, ""
            path_iface = self._peer_path_interface_for_peer(peer)
            if path_iface and interface_family(path_iface) == "serial":
                if self._serial_faster_than_lan(peer):
                    return False, ""
            return True, "LAN available, upgrading from serial"

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
            if (
                att_fam == "serial"
                and self._link_interface_healthy(self.active_link)
                and self._peer_link_active(peer)
            ):
                pass
            else:
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
        if self.dual_identity_mode:
            return False, ""
        if self._connect_in_progress:
            return False, ""
        if self._has_active_transfer():
            return False, ""
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        if not peer or peer == "unknown":
            return False, ""
        if self.is_user_disconnected(peer):
            return False, ""
        if self._peer_expected_transport_families(peer) == {"serial"}:
            if configured_serial_enabled(load_settings_interfaces(self.config_dir)):
                if serial_interface_online() is None:
                    return False, ""
        adopted = self._adopt_healthy_peer_link(peer)
        if adopted:
            if self.active_link and is_serial_interface(
                self._link_attached_interface(self.active_link)
            ):
                return False, ""
            if self.active_link and self._link_interface_healthy(self.active_link):
                needs, reason = self.link_needs_failover()
                if needs:
                    return needs, reason
                return False, ""
        if self._peer_link_active(peer):
            if self.active_link and not self._link_interface_healthy(self.active_link):
                return True, "link interface offline"
            if self.active_link and is_serial_interface(self._link_attached_interface(self.active_link)):
                return False, ""
            if self.active_link:
                needs, reason = self.link_needs_failover()
                if needs:
                    return needs, reason
            return False, ""
        healthy_links = [
            link for link in self._links_for_peer(peer)
            if self._link_interface_healthy(link)
        ]
        if healthy_links:
            self._adopt_healthy_peer_link(peer)
            return False, ""
        in_grace = (time.time() - self._last_link_established_at) < LINK_FAILOVER_GRACE_S
        if in_grace and self._links_for_peer(peer):
            return False, ""
        if self._failover_in_progress:
            return False, ""
        if self.active_link:
            return self.link_needs_failover()
        if self._last_link_lost_at and (time.time() - self._last_link_lost_at) < self._session_reconnect_min_idle():
            return False, ""
        if self._transport_reconnect_pending:
            return True, "transport available — reconnecting"
        if time.time() - self._failover_last_attempt < self._failover_cooldown():
            return False, ""
        return True, "link dropped — reconnecting"

    def clear_session_peer(self):
        self._session_peer_hash = None
        self._session_transport = None

    def _teardown_mismatched_links(self, target_peer):
        """Close links whose resolved peer hash disagrees with the target connect hash."""
        target = self.dest_hash_for(target_peer)
        if not target or target == "unknown":
            return 0
        closed = 0
        for link in list(self.links.values()):
            resolved = self._peer_hash_from_link_identity(link)
            if not resolved or self.hashes_equivalent(resolved, target):
                continue
            try:
                ident = link.get_remote_identity()
                if ident:
                    ident_dest = self._dest_hash_from_identity(ident)
                    if ident_dest and self.hashes_equivalent(ident_dest, target):
                        self._cache_link_peer(link, ident_dest)
                        self._register_peer_link(link, ident_dest)
                        continue
            except Exception:
                pass
            try:
                link.teardown()
                closed += 1
            except Exception:
                pass
        if closed:
            self._pending_sends.clear()
        return closed

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
                score = 85 if not physical_lan_reachable() else 45
            elif fam == "tcp":
                score = 95
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

    def _peer_hash_from_link_identity(self, link):
        if not link:
            return ""
        try:
            ident = link.get_remote_identity()
            if not ident or not getattr(ident, "hash", None):
                return ""
            dest = self._dest_hash_from_identity(ident)
            if dest and not self._is_self_hash(dest):
                return dest
        except Exception:
            pass
        return ""

    def _find_active_link_for_peer(self, dest_hex, alt_hex=None):
        targets = []
        for raw in (dest_hex, alt_hex):
            clean = self.dest_hash_for(raw)
            if clean and clean != "unknown" and clean not in targets:
                targets.append(clean)
        if not targets:
            return None
        for link in list(self.links.values()):
            try:
                if link.status != RNS.Link.ACTIVE:
                    continue
            except Exception:
                continue
            peer = self._peer_hash_from_link_identity(link)
            if not peer or peer == "unknown":
                cached = self._link_peer_hashes.get(link.link_id)
                if cached:
                    peer = self.dest_hash_for(cached)
            if not peer or peer == "unknown":
                continue
            for target in targets:
                if self.hashes_equivalent(peer, target):
                    return link
        return None

    def _resolve_incoming_link_peer(self, link, peer_hash):
        identity_peer = self._peer_hash_from_link_identity(link)
        if identity_peer and identity_peer != "unknown" and not self._is_self_hash(identity_peer):
            return identity_peer
        peer_hash = self.dest_hash_for(peer_hash)
        if is_hub_peer_hash(peer_hash):
            peer_hash = ""
        if peer_hash and peer_hash != "unknown" and not self._is_self_hash(peer_hash):
            if identity_peer and not self.hashes_equivalent(peer_hash, identity_peer):
                peer_hash = ""
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
            if (
                not is_hub_peer_hash(self.active_peer_hash)
                and self._incoming_matches_active_session(link)
                and self._link_acceptable_for_peer(link, self.active_peer_hash)
            ):
                return self.dest_hash_for(self.active_peer_hash)
        return peer_hash or "unknown"

    def _incoming_matches_active_session(self, link):
        if not self.active_peer_hash or not self.active_link:
            return False
        if not self._link_acceptable_for_peer(link, self.active_peer_hash):
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
        return False

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
            self._schedule_queue_drain(peer_hash, link=link, include_files=False, delay=0.5)
        finally:
            self._link_handoff = False

    def _links_for_peer(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return []
        seen = set()
        out = []
        for link in self._other_active_links_for_peer(peer):
            lid = getattr(link, "link_id", None)
            if lid and lid in seen:
                continue
            if lid:
                seen.add(lid)
            out.append(link)
        for cached_key, link in list(self.peer_links.items()):
            if not self.hashes_equivalent(self._peer_from_link_key(cached_key), peer):
                continue
            lid = getattr(link, "link_id", None)
            if lid and lid in seen:
                continue
            if lid:
                seen.add(lid)
            out.append(link)
        return out

    def _best_outgoing_link(self, peer_hash=None):
        """Pick the best link for sends, locked to the peer's transport zone."""
        peer = self.dest_hash_for(
            peer_hash or self.active_peer_hash or self._session_peer_hash or ""
        )
        if not peer or peer == "unknown":
            return None
        session_transport = None
        if self._session_peer_hash and self.hashes_equivalent(peer, self._session_peer_hash):
            session_transport = self._session_transport
        if session_transport:
            preferred = self._link_for_peer(peer, transport=session_transport)
            if preferred and self._link_interface_healthy(preferred):
                if self._link_matches_peer(preferred, peer) and self._link_acceptable_for_peer(preferred, peer):
                    return preferred
        expected = self._peer_expected_transport_families(peer)
        if expected == {"serial"}:
            prefer = ("serial",)
        elif expected & {"udp", "lan", "tcp"}:
            prefer = ("tcp", "lan", "udp")
        else:
            prefer = ("tcp", "lan", "udp", "serial")
        best = None
        best_score = -1
        hub_p2p = self._hub_transport_active() and not self._peer_uses_hub_transport(peer)
        for link in self._links_for_peer(peer):
            if not self._link_matches_peer(link, peer):
                continue
            if not self._link_interface_healthy(link):
                continue
            if not self._link_acceptable_for_peer(link, peer):
                continue
            iface = self._link_attached_interface(link)
            if hub_p2p and self._link_is_hub_transport(iface):
                continue
            fam = interface_family(iface)
            if expected:
                if fam == "serial" and "serial" not in expected:
                    continue
                if fam in ("udp", "lan", "tcp") and not (expected & {"udp", "lan", "tcp"}):
                    continue
            fam_rank = len(prefer) - prefer.index(fam) if fam in prefer else 0
            score = self._link_path_score(link) + fam_rank * 5
            if score > best_score:
                best_score = score
                best = link
        if best:
            return best
        for link in self._links_for_peer(peer):
            if not self._link_matches_peer(link, peer):
                continue
            if not self._link_acceptable_for_peer(link, peer):
                continue
            iface = self._link_attached_interface(link)
            if hub_p2p and self._link_is_hub_transport(iface):
                continue
            try:
                if link.status == RNS.Link.ACTIVE:
                    return link
            except Exception:
                return link
        return self._link_for_peer(peer)

    def _best_transfer_link(self, peer_hash=None):
        """Pick the best link for bulk transfer, respecting serial/LAN transport zones."""
        peer = self.dest_hash_for(
            peer_hash or self.active_peer_hash or self._session_peer_hash or ""
        )
        if not peer or peer == "unknown":
            return None
        expected = self._peer_expected_transport_families(peer)
        if expected == {"serial"}:
            prefer = ("serial",)
        else:
            prefer = ("tcp", "lan", "udp", "serial")
        best = None
        best_score = -1
        for link in self._links_for_peer(peer):
            if not self._link_interface_healthy(link):
                continue
            if not self._link_acceptable_for_peer(link, peer):
                continue
            iface = self._link_attached_interface(link)
            fam = interface_family(iface)
            if expected:
                if fam == "serial" and "serial" not in expected:
                    continue
                if fam in ("udp", "lan", "tcp") and not (expected & {"udp", "lan", "tcp"}):
                    continue
            fam_rank = len(prefer) - prefer.index(fam) if fam in prefer else 0
            score = self._link_path_score(link) + fam_rank * 5
            if score > best_score:
                best_score = score
                best = link
        return best or self._best_outgoing_link(peer)

    def _outgoing_link(self, peer_hash=None):
        if peer_hash:
            link = self._best_outgoing_link(peer_hash)
            if link:
                return link
        if self.active_peer_hash:
            link = self._best_outgoing_link(self.active_peer_hash)
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
        if is_hub_peer_hash(tgt) != is_hub_peer_hash(target_hash):
            return False
        return self.hashes_equivalent(tgt, target_hash)

    def _remove_queue_entry(self, msg_id):
        if not msg_id:
            return False
        before = len(self.message_queue)
        self.message_queue = [
            e for e in self.message_queue if e.get("msg_id") != msg_id
        ]
        if len(self.message_queue) < before:
            self._save_queue()
            print(f"[queue] Confirmed delivery for {msg_id[:8]}")
            return True
        return False

    def _queue_send_link(self, peer_hash, link_hint=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        if link_hint and self._link_matches_peer(link_hint, peer):
            if self._link_acceptable_for_peer(link_hint, peer):
                return link_hint
        best = self._best_outgoing_link(peer)
        if best and self._link_acceptable_for_peer(best, peer):
            return best
        hinted = link_hint if (
            link_hint
            and self._link_matches_peer(link_hint, peer)
            and self._link_acceptable_for_peer(link_hint, peer)
        ) else None
        if hinted:
            return hinted
        fallback = self._link_for_peer(peer)
        if fallback and self._link_acceptable_for_peer(fallback, peer):
            return fallback
        return None

    def _hub_send_targets(self, hub_server_hash=None, hub_server_mode=False):
        tcp_peers = self._hub_tcp_linked_peers()
        if hub_server_mode:
            return tcp_peers
        if hub_server_hash:
            peer = self.dest_hash_for(hub_server_hash)
            if peer and peer != "unknown" and peer in tcp_peers:
                return [peer]
            return []
        return tcp_peers[:1]

    def drain_hub_group_queue(self, hub_server_hash=None, hub_server_mode=False):
        if not any(is_hub_peer_hash(e.get("target_hash")) for e in self.message_queue):
            return 0
        targets = self._hub_send_targets(hub_server_hash, hub_server_mode)
        if not targets or not any(self._peer_link_active(t) for t in targets):
            return 0
        remaining = []
        sent = 0
        for entry in self.message_queue:
            if not is_hub_peer_hash(entry.get("target_hash")):
                remaining.append(entry)
                continue
            if entry.get("type") not in ("text", "emoji"):
                remaining.append(entry)
                continue
            msg_id = entry.get("msg_id")
            result = self.send_hub_message(
                entry["content"],
                msg_id=msg_id,
                hub_server_hash=hub_server_hash,
                hub_server_mode=hub_server_mode,
            )
            if result:
                sent += 1
                if self.on_queue_sent:
                    try:
                        self.on_queue_sent(result, HUB_GROUP_PEER, entry)
                    except Exception as e:
                        print(f"[queue] on_queue_sent error: {e}")
            else:
                remaining.append(entry)
        if sent:
            print(f"[queue] Drained {sent} hub group item(s)")
        self.message_queue = remaining
        self._save_queue()
        return sent

    def _schedule_hub_queue_drain(self, delay=None):
        role, hub_hash = self._load_hub_settings()
        if role == "off":
            return
        wait = QUEUE_DRAIN_DELAY_S if delay is None else delay

        def run():
            try:
                if not self.running:
                    return
                role_now, hub_hash_now = self._load_hub_settings()
                if role_now == "off":
                    return
                self.drain_hub_group_queue(
                    hub_server_hash=hub_hash_now,
                    hub_server_mode=(role_now == "server"),
                )
            except Exception as e:
                print(f"[queue] Hub drain error: {e}")

        timer = threading.Timer(wait, run)
        timer.daemon = True
        timer.start()

    def _schedule_queue_drain(self, peer_hash, link=None, include_files=True, delay=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown" or is_hub_peer_hash(peer):
            return
        if self.is_user_disconnected(peer):
            return
        wait = QUEUE_DRAIN_DELAY_S if delay is None else delay

        def run():
            with self._queue_drain_lock:
                self._queue_drain_timers.pop(peer, None)
            try:
                if not self.running or self.is_user_disconnected(peer):
                    return
                self._drain_queue_for_peer(peer, link_hint=link, include_files=include_files)
            except Exception as e:
                print(f"[queue] Scheduled drain error: {e}")

        with self._queue_drain_lock:
            existing = self._queue_drain_timers.pop(peer, None)
            if existing:
                existing.cancel()
            timer = threading.Timer(wait, run)
            timer.daemon = True
            self._queue_drain_timers[peer] = timer
            timer.start()

    def _drain_queue_for_peer(self, peer_hash, link_hint=None, include_files=True):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown" or is_hub_peer_hash(peer):
            return 0
        if not self._peer_link_active(peer):
            return 0
        send_link = self._queue_send_link(peer, link_hint=link_hint)
        if not send_link:
            return 0
        self._consolidate_peer_links(peer, keep_link=send_link)
        return self.drain_queue(send_link, peer, include_files=include_files)

    def drain_queue(self, link, target_hash, include_files=True):
        peer = self.dest_hash_for(target_hash)
        if not peer or is_hub_peer_hash(peer):
            return 0
        send_link = self._queue_send_link(peer, link_hint=link)
        if not send_link:
            return 0
        remaining = []
        sent = 0
        confirmed_ids = set()
        now = time.time()
        for entry in self.message_queue:
            if not self._queue_matches_target(entry, peer):
                remaining.append(entry)
                continue
            try:
                if entry["type"] in ("text", "emoji"):
                    sent_at = entry.get("_queue_sent_at")
                    if sent_at and (now - sent_at) < QUEUE_RECEIPT_TIMEOUT_S:
                        remaining.append(entry)
                        continue
                    if sent_at:
                        entry.pop("_queue_sent_at", None)
                    msg_id = entry.get("msg_id")
                    sent_msg = []

                    def on_receipt(status, receipt, mid=msg_id, qentry=entry):
                        if status not in ("received", "read"):
                            return
                        confirmed_ids.add(mid)
                        self._remove_queue_entry(mid)
                        if self.on_queue_sent and sent_msg:
                            try:
                                self.on_queue_sent(sent_msg[0], peer, qentry)
                            except Exception as e:
                                print(f"[queue] on_queue_sent error: {e}")

                    result = self.send_message(
                        entry["content"],
                        msg_id=msg_id,
                        target_peer=peer,
                        link=send_link,
                        receipt_callback=on_receipt,
                    )
                    if result:
                        sent_msg.append(result)
                        entry["_queue_sent_at"] = time.time()
                        sent += 1
                        if msg_id not in confirmed_ids:
                            remaining.append(entry)
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
                            target_peer=peer,
                            link=send_link,
                        )
                        if result:
                            sent += 1
                            if self.on_queue_sent:
                                try:
                                    self.on_queue_sent(result, peer, entry)
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
            print(
                f"[queue] Drained {sent} queued item(s) for {peer[:16]}... "
                f"(awaiting receipt)"
            )
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
            if not self._peer_link_active(peer):
                continue
            link = self._queue_send_link(peer)
            if not link:
                continue
            try:
                if getattr(link, "status", None) != RNS.Link.ACTIVE:
                    continue
            except Exception:
                pass
            sent += self.drain_queue(link, peer, include_files=True)
        role, hub_hash = self._load_hub_settings()
        if role != "off":
            sent += self.drain_hub_group_queue(
                hub_server_hash=hub_hash,
                hub_server_mode=(role == "server"),
            )
        return sent

    def queue_size(self):
        return len(self.message_queue)

    def queue_size_for(self, target_hash=None):
        if not target_hash:
            return len(self.message_queue)
        return sum(
            1 for entry in self.message_queue
            if self._queue_matches_target(entry, target_hash)
        )

    def prune_stale_queue(self, sent_msg_ids=None):
        """Drop queue rows already marked sent in chat history."""
        sent = set(sent_msg_ids or [])
        if not sent:
            return 0
        before = len(self.message_queue)
        self.message_queue = [
            e for e in self.message_queue
            if e.get("msg_id") not in sent
        ]
        if len(self.message_queue) != before:
            self._save_queue()
        return before - len(self.message_queue)

    def _setup_inbound_destination(self, identity, attr_name):
        if not identity:
            return None
        dest = RNS.Destination(
            identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            APP_NAME,
            "messages",
        )
        dest.set_proof_strategy(RNS.Destination.PROVE_ALL)
        dest.accepts_links(True)
        dest.set_link_established_callback(self._link_callback)
        setattr(self, attr_name, dest)
        dest_hex = normalize_hash(RNS.hexrep(dest.hash))
        ident_hex = normalize_hash(RNS.hexrep(identity.hash))
        self.register_peer_mapping(dest_hex, ident_hex)
        return dest

    def start(self):
        self.destination = self._setup_inbound_destination(self.identity, "destination")
        if self.identity_serial:
            self.destination_serial = self._setup_inbound_destination(
                self.identity_serial, "destination_serial",
            )
            self.my_dest_hash_serial = normalize_hash(
                RNS.hexrep(self.destination_serial.hash),
            )
            print(f"[identity] Serial endpoint {self.my_dest_hash_serial[:16]}...")

        if self.auto_announce:
            self._announce(also_serial=False)
            self._announce_thread = threading.Thread(target=self._announce_loop, daemon=True)
            self._announce_thread.start()

        self.running = True
        self._queue_retry_thread = threading.Thread(target=self._queue_retry_loop, name="chatxz-queue-retry", daemon=True)
        self._queue_retry_thread.start()
        try:
            from chatxz.core.rns_interfaces import register_serial_hot_add_callback
            register_serial_hot_add_callback(self.on_serial_transport_attached)
        except Exception:
            pass
        print(f"[messaging] Started (auto_announce={self.auto_announce})")
        return self.destination

    def on_serial_transport_attached(self, iface=None):
        """USB serial became available — announce on serial and nudge reconnect."""
        if not self.running or not self.destination:
            return
        if self._has_active_transfer():
            return
        if not self.ensure_serial_runtime():
            print("[serial] USB attached but serial identity not ready")
            return
        sent = self._burst_serial_announce(count=1, force=True)
        if sent:
            port = getattr(iface, "port", "?") if iface else "?"
            print(f"[serial] Auto-announced on serial attach ({port})")
        peer = self.dest_hash_for(self.active_peer_hash or self._session_peer_hash or "")
        if peer and not is_hub_peer_hash(peer) and not self.is_user_disconnected(peer):
            request_paths_for_hash(peer, family="serial")
            if not self._peer_link_active(peer):
                self._transport_reconnect_pending = True
                self._failover_last_attempt = 0

    def on_serial_transport_detached(self):
        """USB serial unplugged — drop serial paths and stop serial-only routing."""
        from chatxz.core.lan_rns import clear_paths_on_family, unpin_serial_path

        try:
            clear_paths_on_family("serial")
        except Exception:
            pass
        for peer_hash in list(self.peer_links.keys()):
            unpin_serial_path(peer_hash)
        self._transport_reconnect_pending = False

    def _queue_retry_loop(self):
        while self.running:
            for _ in range(QUEUE_RETRY_INTERVAL_S):
                if not self.running:
                    return
                time.sleep(1)
            if self.message_queue:
                try:
                    self.retry_queue()
                except Exception as e:
                    print(f"[queue] Retry loop error: {e}")

    def announce(self, also_serial=True):
        self._announce(also_serial=also_serial)

    def _serial_mode_active(self):
        return (
            configured_serial_enabled(load_settings_interfaces(self.config_dir))
            and not lan_discovery_configured(load_settings_interfaces(self.config_dir))
        )

    def _announce_payload(self, include_lan_ip=True):
        payload = {
            "app": APP_NAME,
            "name": self.display_name or "",
        }
        if include_lan_ip and lan_discovery_configured(load_settings_interfaces(self.config_dir)):
            try:
                from chatxz.utils.platform import discovery_scope_ip

                ip = (discovery_scope_ip() or "").strip()
                if ip:
                    payload["ip"] = ip
            except Exception:
                pass
        return json.dumps(payload).encode("utf-8")

    def _peer_lan_ip_usable(self, peer_ip):
        """False when peer IPv4 is outside our pinned LAN scope (use serial instead)."""
        host = (peer_ip or "").strip()
        if not host:
            return False
        try:
            from chatxz.utils.platform import discovery_scope_ip
            from chatxz.utils.lan_scope import peer_in_scope

            scope = (discovery_scope_ip() or "").strip()
            if not scope:
                return True
            return peer_in_scope(host, scope)
        except Exception:
            return True

    def _announce_on_interface(self, iface, app_data=None):
        if is_serial_interface(iface) and not self.ensure_serial_runtime():
            return False
        dest = self._destination_for_interface(iface)
        if not dest or not iface:
            return False
        data = app_data if app_data is not None else self._announce_payload()
        if is_serial_interface(iface):
            try:
                payload = json.loads(data.decode("utf-8"))
                payload.pop("ip", None)
                data = json.dumps(payload).encode("utf-8")
            except Exception:
                pass
        dest.announce(app_data=data, attached_interface=iface)
        if is_serial_interface(iface):
            try:
                if self.identity_serial:
                    self.identity_serial.announce(attached_interface=iface)
            except Exception:
                pass
        else:
            try:
                if self.identity:
                    self.identity.announce(attached_interface=iface)
            except Exception:
                pass
        return True

    def _fallback_announce(self, announce_data):
        """Last-resort announce — never fan out LAN IP on USB when serial is up."""
        if self._serial_transport_ready():
            self._burst_serial_announce(count=1)
            return
        self.destination.announce(app_data=announce_data)
        try:
            RNS.Transport.identity.announce()
        except Exception:
            pass

    def _burst_serial_announce(self, count=None, interval=None, force=False):
        """Send RNS announces on serial only (default: one packet)."""
        if not force and (
            self._connect_in_progress
            or self._failover_in_progress
            or self._has_active_transfer()
        ):
            return 0
        if not self._serial_transport_ready():
            return 0
        if not self.ensure_serial_runtime():
            return 0
        suppress_offline_lan_transports()
        dedupe_serial_interfaces()
        prune_dead_serial_interfaces()
        iface = serial_interface_online()
        if not iface:
            return 0
        burst = count or SERIAL_ANNOUNCE_BURST_COUNT
        gap = interval if interval is not None else SERIAL_ANNOUNCE_BURST_INTERVAL_S
        announce_data = self._announce_payload(include_lan_ip=False)
        for attempt in range(burst):
            self._announce_on_interface(iface, app_data=announce_data)
            if attempt < burst - 1 and gap > 0:
                time.sleep(gap)
        port = getattr(iface, "port", "?")
        if burst <= 1:
            print(f"[serial] RNS announce on {port}")
        else:
            print(f"[serial] Burst {burst} RNS announce(s) on {port}")
        return burst

    def _silent_announce(self, peer_ip=None, also_serial=None):
        """RNS path refresh only — no subnet beacon probe."""
        if also_serial is None:
            also_serial = not self._failover_in_progress
        if not self.destination:
            return
        announce_data = self._announce_payload()
        interfaces = load_settings_interfaces(self.config_dir)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        udp_lan = configured_udp_lan_enabled(interfaces)
        if not physical_lan_reachable():
            suppress_offline_lan_transports()
            if self._serial_transport_ready():
                self._burst_serial_announce(count=1)
            return
        prune_dead_serial_interfaces()
        hub_role, _ = self._load_hub_settings()
        use_tcp_lan = tcp_lan and hub_role != "server"
        if use_tcp_lan:
            ensure_runtime_tcp_lan_server(config_dir=self.config_dir)
            if peer_ip:
                ensure_tcp_client_to_peer(peer_ip, config_dir=self.config_dir)
            tcp_iface = tcp_server_interface_online() or tcp_client_interface_online()
            if tcp_iface:
                self._announce_on_interface(tcp_iface, app_data=announce_data)
            elif self._serial_transport_ready():
                self._burst_serial_announce(count=1)
                return
            else:
                self._fallback_announce(announce_data)
        elif udp_lan:
            udp_iface = udp_interface_online()
            if udp_iface:
                self._announce_on_interface(udp_iface, app_data=announce_data)
            elif self._serial_transport_ready():
                self._burst_serial_announce(count=1)
                return
            else:
                self._fallback_announce(announce_data)
        elif self._serial_transport_ready():
            self._burst_serial_announce(count=1)
            return
        else:
            self._fallback_announce(announce_data)
        if peer_ip and udp_lan:
            packet = build_announce_packet(self.destination, announce_data)
            unicast_announce_packet(packet, peer_ip=peer_ip, subnet_probe=False)

    def _announce(self, peer_ip=None, unicast_subnet=None, also_serial=True):
        if not self.destination:
            return
        announce_data = self._announce_payload()
        if not physical_lan_reachable() and self._serial_transport_ready():
            if also_serial:
                self._burst_serial_announce(count=1)
            return
        prune_dead_serial_interfaces()
        interfaces = load_settings_interfaces(self.config_dir)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        udp_lan = configured_udp_lan_enabled(interfaces)
        hub_role, _ = self._load_hub_settings()
        use_tcp_lan = tcp_lan and hub_role != "server"
        if use_tcp_lan:
            ensure_runtime_tcp_lan_server(config_dir=self.config_dir)
            tcp_iface = tcp_server_interface_online() or tcp_client_interface_online()
            if tcp_iface:
                self._announce_on_interface(tcp_iface, app_data=announce_data)
            else:
                self._fallback_announce(announce_data)
        elif udp_lan:
            udp_iface = udp_interface_online()
            if udp_iface:
                self._announce_on_interface(udp_iface, app_data=announce_data)
            else:
                self._fallback_announce(announce_data)
        elif self._serial_transport_ready():
            self._burst_serial_announce(count=1)
        else:
            self._fallback_announce(announce_data)
        if unicast_subnet is None:
            unicast_subnet = True
        lan_ok = (
            lan_ip_reachable()
            and lan_discovery_configured(load_settings_interfaces(self.config_dir))
        )
        if udp_lan and (peer_ip or (unicast_subnet and lan_ok)):
            packet = build_announce_packet(self.destination, announce_data)
            sent = unicast_announce_packet(
                packet,
                peer_ip=peer_ip,
                subnet_probe=unicast_subnet and lan_ok,
            )
            if sent:
                hint = f" + {sent} unicast" if sent else ""
                print(f"[messaging] Announced on LAN (name={self.display_name or 'none'}{hint})")
                if also_serial and self._serial_transport_ready() and configured_serial_enabled(interfaces):
                    self._burst_serial_announce(count=1)
                return
        if (
            also_serial
            and self._serial_transport_ready()
            and configured_serial_enabled(interfaces)
            and lan_ok
        ):
            self._burst_serial_announce(count=1)
        if lan_ok:
            print(f"[messaging] Announced on LAN (name={self.display_name or 'none'})")
        else:
            print(f"[messaging] Announced on RNS (serial/other — LAN disconnected)")

    def _lan_transport_ready(self):
        interfaces = load_settings_interfaces(self.config_dir)
        if not lan_discovery_configured(interfaces):
            return False
        udp_lan = configured_udp_lan_enabled(interfaces)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        if is_android():
            if tcp_lan and not udp_lan:
                return (
                    tcp_server_interface_online() is not None
                    or lan_mesh_has_peer()
                    or bool(online_interfaces(family="tcp"))
                )
            return lan_mesh_has_peer() or bool(online_interfaces(family="udp"))
        if not physical_lan_reachable():
            return lan_mesh_has_peer()
        if tcp_lan and not udp_lan:
            return (
                lan_mesh_has_peer()
                or tcp_server_interface_online() is not None
                or bool(online_interfaces(family="tcp"))
            )
        return lan_mesh_has_peer() or bool(online_interfaces(family="udp"))

    def _serial_transport_ready(self):
        return serial_interface_online() is not None

    def _mark_peer_lan_unreachable(self, peer_ip):
        peer_ip = (peer_ip or "").strip()
        if peer_ip:
            self._peer_lan_unreachable[peer_ip] = time.time() + PEER_LAN_UNREACHABLE_TTL_S

    def _clear_peer_lan_unreachable(self, peer_ip):
        self._peer_lan_unreachable.pop((peer_ip or "").strip(), None)

    def _peer_lan_recently_unreachable(self, peer_ip):
        peer_ip = (peer_ip or "").strip()
        if not peer_ip:
            return False
        return time.time() < self._peer_lan_unreachable.get(peer_ip, 0)

    def _ensure_runtime_serial_transport(self):
        try:
            return ensure_runtime_serial(load_settings_interfaces(self.config_dir))
        except Exception as exc:
            print(f"[serial] Runtime serial ensure failed: {exc}")
            return None

    def _http_peer_post(self, peer_ip, peer_port, path, payload=None, timeout=HTTP_WAKE_TIMEOUT_S):
        if not peer_ip or self._interrupted() or not physical_lan_reachable():
            return False
        if self.shutdown_requested:
            timeout = min(timeout, 0.5)
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
        if not peer_ip or self._interrupted() or not physical_lan_reachable():
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
        ok = results["connect"] or results["announce"]
        if ok:
            self._clear_peer_lan_unreachable(peer_ip)
        else:
            self._mark_peer_lan_unreachable(peer_ip)
        return ok

    def _prime_udp_path(self, dest_hex, peer_ip=None, timeout_s=None):
        """Establish a UDP RNS path before opening a link (required for Android peers)."""
        if self._interrupted():
            return False
        if timeout_s is None:
            timeout_s = 6.0 if is_android() else 4.0
        self._silent_announce(peer_ip=peer_ip if physical_lan_reachable() else None)
        request_paths_for_hash(dest_hex, family="udp")
        path_iface = wait_for_peer_path_families(
            dest_hex, families=("udp",), timeout_s=timeout_s, should_stop=self._interrupted,
        )
        if path_iface:
            print(f"[connect] UDP path ready via {type(path_iface).__name__}")
            return True
        return False

    def _prime_tcp_path(self, dest_hex, peer_ip=None, timeout_s=None):
        """Establish a TCP LAN RNS path before opening a link."""
        if self._interrupted():
            return False
        if timeout_s is None:
            timeout_s = 6.0 if is_android() else 4.0
        if peer_ip:
            ensure_tcp_client_to_peer(peer_ip, config_dir=self.config_dir)
        self._silent_announce(peer_ip=peer_ip if physical_lan_reachable() else None)
        request_paths_for_hash(dest_hex, family="tcp")
        path_iface = wait_for_peer_path_families(
            dest_hex, families=("tcp",), timeout_s=timeout_s, should_stop=self._interrupted,
        )
        if path_iface:
            print(f"[connect] TCP path ready via {type(path_iface).__name__}")
            return True
        return False

    def _prime_lan_path(self, dest_hex, peer_ip=None, timeout_s=None):
        """Prime UDP or TCP LAN path depending on configured transport."""
        interfaces = load_settings_interfaces(self.config_dir)
        if configured_tcp_lan_enabled(interfaces) and not configured_udp_lan_enabled(interfaces):
            return self._prime_tcp_path(dest_hex, peer_ip=peer_ip, timeout_s=timeout_s)
        return self._prime_udp_path(dest_hex, peer_ip=peer_ip, timeout_s=timeout_s)

    def _prime_serial_path(self, dest_hex, timeout_s=None):
        """Establish an RNS path over USB serial (no LAN/HTTP wake required)."""
        if not self._serial_transport_ready():
            print("[connect] Serial path blocked — Serial in RNS: no")
            return False
        clear_peer_path_unless_family(dest_hex, "serial")
        suppress_offline_lan_transports()
        dedupe_serial_interfaces()
        restored = restore_serial_path_from_announce(dest_hex)
        if restored:
            print(f"[connect] Serial path ready via {type(restored).__name__} (announce)")
            return True
        if self._peer_has_path_on_family(dest_hex, "serial"):
            return True
        if timeout_s is None:
            timeout_s = SERIAL_PATH_PRIME_TIMEOUT_S
        print(f"[connect] Priming serial RNS path ({timeout_s:.0f}s)...")
        deadline = time.time() + timeout_s
        last_burst = 0.0
        while time.time() < deadline:
            if self._interrupted():
                return False
            now = time.time()
            if now - last_burst >= SERIAL_CONNECT_PRIME_INTERVAL_S:
                self._burst_serial_announce(count=1, force=True)
                reinforce_serial_peer_path(dest_hex)
                last_burst = now
            restored = restore_serial_path_from_announce(dest_hex)
            if restored:
                print(f"[connect] Serial path ready via {type(restored).__name__} (announce)")
                return True
            path_iface = wait_for_peer_path_families(
                dest_hex, families=("serial",), timeout_s=2.0, poll_s=0.2,
                should_stop=self._interrupted,
            )
            if path_iface:
                print(f"[connect] Serial path ready via {type(path_iface).__name__}")
                return True
        print(
            "[connect] Serial path not ready — both ends need Serial in RNS: yes, "
            "same baud, tap Announce on each, then Connect"
        )
        return False

    def _connect_serial_peer(self, destination, dest_hex, clean, old_link=None,
                             prime_timeout=8.0):
        """Single serial connect: prime once, brief inbound wait, outbound, inbound fallback."""
        if not self._serial_transport_ready():
            print("[connect] Serial path blocked — Serial in RNS: no")
            return False
        pin_serial_path(dest_hex)
        try:
            clear_peer_path_unless_family(dest_hex, "serial")
            prune_lan_path_for_peer(dest_hex)
            suppress_offline_lan_transports()
            dedupe_serial_interfaces()
            restored = restore_serial_path_from_announce(dest_hex)
            if restored:
                print(f"[connect] Serial path ready via {type(restored).__name__} (announce)")
            elif not self._peer_has_path_on_family(dest_hex, "serial"):
                if not self._prime_serial_path(dest_hex, timeout_s=prime_timeout):
                    return False
            else:
                print("[connect] Serial path ready via SerialInterface")
            ensure_serial_path_pinned(dest_hex)
            print(
                f"[connect] Serial peer — listening for inbound "
                f"({SERIAL_INBOUND_FIRST_WAIT_S}s)..."
            )
            if self._wait_for_peer_link(
                dest_hex, alt_hex=clean, timeout_s=SERIAL_INBOUND_FIRST_WAIT_S,
            ):
                return True
            ensure_serial_path_pinned(dest_hex)
            print(f"[connect] Serial outbound ({SERIAL_LINK_CONNECT_TIMEOUT_S}s)...")
            if self._establish_outbound_link(
                destination, dest_hex, clean, old_link=old_link,
                timeout_s=SERIAL_LINK_CONNECT_TIMEOUT_S, serial=True,
            ):
                return True
            if self._peer_link_active(dest_hex, clean):
                return True
            print(f"[connect] Waiting for serial inbound ({SERIAL_INBOUND_WAIT_S}s)...")
            if self._wait_for_peer_link(
                dest_hex, alt_hex=clean, timeout_s=SERIAL_INBOUND_WAIT_S,
            ):
                return True
            return False
        finally:
            unpin_serial_path(dest_hex)

    def _promote_outbound_link(self, link, dest_hex, old_link=None, promote_active=None):
        if not link:
            return False
        try:
            if link.status != RNS.Link.ACTIVE:
                return False
        except Exception:
            return False
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
                getattr(self, "_connect_failover", False)
                or (self._connect_user_initiated and not self._connect_background)
                or (
                    self.active_peer_hash
                    and self.hashes_equivalent(dest_hex, self.active_peer_hash)
                )
                or (
                    self._session_peer_hash
                    and self.hashes_equivalent(dest_hex, self._session_peer_hash)
                )
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
            iface = self._link_attached_interface(link)
            ident = (
                self.identity_serial
                if iface and is_serial_interface(iface) and self.identity_serial
                else self.identity
            )
            if ident:
                link.identify(ident)
        except Exception:
            pass
        print("[connect] Link established")
        self._schedule_queue_drain(
            dest_hex, link=link, include_files=not self._has_active_transfer(),
        )
        return True

    def _establish_outbound_link(self, destination, dest_hex, clean, old_link=None,
                                 timeout_s=LINK_CONNECT_TIMEOUT_S, promote_active=None,
                                 serial=False):
        """Try to open an outbound RNS link within timeout_s."""
        link = None
        try:
            if serial:
                ensure_serial_path_pinned(dest_hex)
            link_ctx = (
                boost_serial_establishment_timeout(timeout_s)
                if serial else _null_context()
            )
            with link_ctx:
                link = RNS.Link(destination)
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                if self._interrupted():
                    self._teardown_outbound_attempt(link)
                    return False
                if serial:
                    ensure_serial_path_pinned(dest_hex, request=False)
                time.sleep(LINK_CONNECT_POLL_S)
                if self._peer_link_active(dest_hex, clean):
                    existing = self._link_for_peer(dest_hex) or self._link_for_peer(clean)
                    if existing:
                        self._notify_link_established(
                            existing, dest_hex,
                            promote_active=True, background=False,
                        )
                    self._teardown_outbound_attempt(link)
                    return True
                try:
                    if link.status == RNS.Link.ACTIVE:
                        return self._promote_outbound_link(
                            link, dest_hex, old_link=old_link, promote_active=promote_active,
                        )
                    if link.status == RNS.Link.CLOSED:
                        break
                except Exception:
                    pass
                if self.active_link and link and self.active_link.link_id == link.link_id:
                    return True
            if self._promote_outbound_link(
                link, dest_hex, old_link=old_link, promote_active=promote_active,
            ):
                return True
            if self._adopt_healthy_peer_link(dest_hex):
                return True
        except Exception as e:
            print(f"[connect] Link failed: {e}")
        finally:
            active = False
            try:
                active = link and link.status == RNS.Link.ACTIVE
            except Exception:
                active = False
            if not active and not self._peer_link_active(dest_hex, clean):
                self._teardown_outbound_attempt(link)
        if self._adopt_healthy_peer_link(dest_hex):
            return True
        return self._peer_link_active(dest_hex, clean)

    def _peer_link_active(self, dest_hex, alt_hex=None, transport=None):
        for raw in (dest_hex, alt_hex):
            if not raw:
                continue
            peer = self.dest_hash_for(raw)
            link = self._link_for_peer(peer, transport=transport)
            if not link:
                continue
            try:
                active = link.status == RNS.Link.ACTIVE
            except Exception:
                active = True
            if (
                active
                and self._link_matches_peer(link, peer)
                and self._link_acceptable_for_peer(link, peer)
                and self._link_transport_matches(link, transport)
            ):
                return True
        if transport:
            return False
        found = self._find_active_link_for_peer(dest_hex, alt_hex)
        if not found:
            return False
        peer = self.dest_hash_for(dest_hex or alt_hex)
        return (
            self._link_matches_peer(found, peer)
            and self._link_acceptable_for_peer(found, peer)
        )

    def _peer_link_usable(self, dest_hex, alt_hex=None, transport=None):
        """True when an active link also has a healthy interface and RNS path."""
        if not self._peer_link_active(dest_hex, alt_hex, transport=transport):
            return False, None
        peer = self.dest_hash_for(dest_hex)
        adopt = (
            self._link_for_peer(peer, transport=transport)
            or self._find_active_link_for_peer(dest_hex, alt_hex)
        )
        if not adopt:
            return False, None
        if not self._link_interface_healthy(adopt) or not self._peer_has_path(dest_hex):
            return False, adopt
        return True, adopt

    def _wait_for_peer_link(self, dest_hex, alt_hex=None, timeout_s=REVERSE_CONNECT_WAIT_S):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._interrupted():
                return False
            if serial_path_is_pinned(dest_hex) or serial_path_is_pinned(alt_hex or ""):
                ensure_serial_path_pinned(dest_hex, request=False)
            if self._peer_link_active(dest_hex, alt_hex):
                found = self._find_active_link_for_peer(dest_hex, alt_hex)
                if found and not self._link_for_peer(dest_hex):
                    self._notify_link_established(
                        found, dest_hex, promote_active=True, background=False,
                    )
                else:
                    self._adopt_healthy_peer_link(dest_hex)
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
                if link.link_id in self.links:
                    del self.links[link.link_id]
        except Exception:
            pass

    def _should_periodic_announce(self):
        """True when periodic LAN/serial RNS refresh may run."""
        if not self.auto_announce:
            return False
        if (
            self._connect_in_progress
            or self._failover_in_progress
            or self._has_active_transfer()
        ):
            return False
        interfaces = load_settings_interfaces(self.config_dir)
        return (
            lan_discovery_configured(interfaces)
            or (
                configured_serial_enabled(interfaces)
                and self._serial_transport_ready()
            )
        )

    def _announce_loop(self):
        lan_tick = 0
        serial_tick = 0
        while self.running:
            time.sleep(1)
            if not self.running:
                return
            if self._has_active_transfer() or not self._should_periodic_announce():
                continue
            interfaces = load_settings_interfaces(self.config_dir)
            prune_dead_serial_interfaces()
            lan_iv = max(0, int(self.lan_announce_interval_s or 0))
            ser_iv = max(0, int(self.serial_announce_interval_s or 0))
            if lan_iv <= 0 and ser_iv <= 0 and not self.auto_announce:
                continue
            if lan_iv <= 0 and self.auto_announce:
                lan_iv = self.announce_interval
            if ser_iv <= 0 and self.auto_announce:
                ser_iv = self.announce_interval
            lan_tick += 1
            serial_tick += 1
            if lan_iv > 0 and lan_tick >= lan_iv and lan_discovery_configured(interfaces):
                lan_tick = 0
                self._silent_announce(also_serial=False)
            if (
                ser_iv > 0
                and serial_tick >= ser_iv
                and configured_serial_enabled(interfaces)
                and self._serial_transport_ready()
            ):
                serial_tick = 0
                self._burst_serial_announce(count=1)

    def cancel_all_transfers(self):
        """Abort in-flight file sends/receives during shutdown."""
        self.shutdown_requested = True
        for tid in list(self._active_resources.keys()):
            self.cancel_transfer(transfer_id=tid)
        if self._current_transfer_id:
            self.cancel_transfer(transfer_id=self._current_transfer_id)

    def stop(self):
        self.running = False
        self.shutdown_requested = True
        self.cancel_all_transfers()
        for link_id, link in self.links.items():
            try:
                link.teardown()
            except:
                pass

    def rebind_identity(self, identity, role="lan"):
        """Hot-swap LAN or serial identity without restarting the process."""
        role = (role or "lan").strip().lower()
        self.disconnect_all_peers(clear_session=True)
        self.identity_to_dest.clear()
        self.dest_to_identity.clear()
        self._link_peer_hashes.clear()
        self.peer_links.clear()
        self.links.clear()
        self.active_link = None
        self.active_peer_hash = None
        self._send_link = None
        self._session_peer_hash = None
        if role == "serial":
            self.identity_serial = identity
            self.destination_serial = self._setup_inbound_destination(
                identity, "destination_serial",
            )
            self.my_dest_hash_serial = normalize_hash(
                RNS.hexrep(self.destination_serial.hash),
            )
            dest_hex = self.my_dest_hash_serial
            try:
                self._burst_serial_announce(count=1, force=True)
            except Exception as e:
                print(f"[identity] Post-rebind serial announce failed: {e}")
        else:
            self.identity = identity
            self.destination = self._setup_inbound_destination(identity, "destination")
            dest_hex = normalize_hash(RNS.hexrep(self.destination.hash))
            self.my_dest_hash = dest_hex
            try:
                self._silent_announce(also_serial=False)
            except Exception as e:
                print(f"[identity] Post-rebind LAN announce failed: {e}")
        print(f"[identity] Rebound {role} destination to {dest_hex[:16]}...")
        return self.destination_serial if role == "serial" else self.destination

    def _dest_hash_from_identity(self, ident):
        dest = message_dest_hash_for_identity(ident)
        if dest and ident and getattr(ident, "hash", None):
            ident_hex = normalize_hash(RNS.hexrep(ident.hash))
            if ident_hex and ident_hex != dest:
                self.register_peer_mapping(dest, ident_hex)
        return dest

    def _recall_identity_bytes(self, raw):
        if not raw:
            return None
        ident = RNS.Identity.recall(raw)
        if ident is None:
            ident = RNS.Identity.recall(raw, from_identity_hash=True)
        return ident

    def _identity_hash_candidates(self, hash_hex):
        clean = normalize_hash(hash_hex)
        if len(clean) != 32:
            return []
        candidates = [clean]
        mapped_dest = self.dest_hash_for(clean)
        if mapped_dest and mapped_dest not in candidates:
            candidates.append(mapped_dest)
        ident_hex = self.dest_to_identity.get(clean)
        if ident_hex and ident_hex not in candidates:
            candidates.append(ident_hex)
        for ih, dest in self.identity_to_dest.items():
            if ih == clean or dest == clean:
                for h in (ih, dest):
                    if h and h not in candidates:
                        candidates.append(h)
        return candidates

    def _identity_for_hash(self, hash_hex):
        for candidate in self._identity_hash_candidates(hash_hex):
            try:
                raw = bytes.fromhex(candidate)
            except Exception:
                continue
            ident = self._recall_identity_bytes(raw)
            if ident:
                dest = message_dest_hash_for_identity(ident)
                if dest:
                    self.register_peer_mapping(
                        dest, normalize_hash(RNS.hexrep(ident.hash))
                    )
                return ident
        return None

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
        serial_wait = (
            not lan_discovery_configured(load_settings_interfaces(self.config_dir))
            and self._serial_transport_ready()
        )
        if serial_wait:
            wait_s = SERIAL_IDENTITY_WAIT_TIMEOUT_S
        elif is_android():
            wait_s = ANDROID_IDENTITY_WAIT_TIMEOUT_S
        else:
            wait_s = IDENTITY_WAIT_TIMEOUT_S
        deadline = time.time() + wait_s
        last_log = 0
        last_burst = 0.0
        while time.time() < deadline:
            ident = self._identity_for_hash(clean)
            if ident:
                return ident, clean

            if peer_lookup:
                peer = peer_lookup(peer_ip, clean)
                if peer:
                    if register_identity_from_peer(peer):
                        for candidate in self._identity_hash_candidates(clean):
                            ident = self._identity_for_hash(candidate)
                            if ident:
                                resolved = self._hash_from_peer_info(peer) or candidate
                                print(
                                    f"[connect] Identity registered from beacon "
                                    f"({peer.get('ip', '?')}): {resolved[:16]}..."
                                )
                                return ident, resolved
                    alt = self._hash_from_peer_info(peer)
                    if alt and alt != clean:
                        clean = alt
                        ident = self._identity_for_hash(clean)
                        if ident:
                            print(f"[connect] Resolved peer via discovery: {clean[:16]}...")
                            return ident, clean
                    for key in ("hash", "identity_hash"):
                        alt = normalize_hash(peer.get(key))
                        if not alt or alt == clean:
                            continue
                        ident = self._identity_for_hash(alt)
                        if ident:
                            resolved = self._hash_from_peer_info(peer) or alt
                            print(f"[connect] Resolved peer via discovery: {resolved[:16]}...")
                            return ident, resolved

            now = time.time()
            if now - last_log >= 3:
                remaining = int(deadline - now)
                hint = " (serial — tap Announce on peer too)" if serial_wait else ""
                print(f"[connect] Waiting for peer identity ({remaining}s left){hint}...")
                last_log = now
            if serial_wait:
                if now - last_burst >= 2.0:
                    self._burst_serial_announce(count=1)
                    request_paths_for_hash(clean, family="serial")
                    last_burst = now
            elif not lan_discovery_configured(load_settings_interfaces(self.config_dir)):
                self._silent_announce()
                request_paths_for_hash(clean, family="serial")
            else:
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

    def _notify_link_established(self, link, peer_hash=None, promote_active=True,
                                 background=False, passive=False):
        peer = self.canonical_connect_hash(peer_hash or "", link=link)
        if (not peer or peer == "unknown") and link:
            peer = self._peer_hash_from_link_identity(link)
        if not peer or peer == "unknown":
            peer = self.canonical_connect_hash(
                self._peer_destination_hash(link, fallback=peer_hash),
                link=link,
            )
        if is_hub_peer_hash(peer):
            return
        if not peer or peer == "unknown":
            session_peer = self.dest_hash_for(self._session_peer_hash or "")
            if session_peer and not is_hub_peer_hash(session_peer):
                peer = session_peer
        if not peer or peer == "unknown":
            return
        self._register_peer_link(link, peer)
        self._last_link_established_at = time.time()
        if promote_active:
            self._consolidate_peer_links(peer, keep_link=link)
            session_peer = self.dest_hash_for(self._session_peer_hash or "")
            parallel = self._parallel_sessions_allowed()
            adopt_session = (
                not parallel
                or not session_peer
                or self.hashes_equivalent(peer, session_peer)
                or not self.active_link
            )
            old_active = self.active_peer_hash
            if adopt_session:
                self.active_link = link
                self.active_peer_hash = peer
                self._session_peer_hash = peer
                self._send_link = link
                if not old_active or self.hashes_equivalent(peer, old_active):
                    self._pending_sends.clear()
            else:
                self._register_peer_link(link, peer)
        label = "background" if background else "active"
        print(f"[messaging] Link ready with {peer[:16]}... ({label})")
        if self.on_link_established:
            try:
                self.on_link_established(
                    peer, link,
                    background=background,
                    promote_active=promote_active,
                    passive=passive,
                )
            except TypeError:
                try:
                    self.on_link_established(
                        peer, link, background=background, promote_active=promote_active,
                    )
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

    def _optimise_link_mtu(self, link):
        try:
            iface = self._link_attached_interface(link)
            if is_serial_interface(iface):
                tune_serial_link(link, iface)
                print(
                    f"[messaging] Serial link tuned for file transfer "
                    f"(MTU {getattr(link, 'mtu', '?')}, window=2)"
                )
                return
            hw_mtu = getattr(iface, "HW_MTU", None) if iface else None
            current = int(getattr(link, "mtu", 500) or 500)
            if hw_mtu and current < hw_mtu:
                link.mtu = int(hw_mtu)
                link.update_mdu()
                print(
                    f"[messaging] Link MTU upgraded {current} -> {link.mtu} "
                    f"({type(iface).__name__ if iface else 'iface'})"
                )
        except Exception as exc:
            print(f"[messaging] Link MTU upgrade skipped: {exc}")

    def _peer_endpoint(self, peer_hash):
        if self.peer_endpoint_resolver:
            try:
                endpoint = self.peer_endpoint_resolver(peer_hash)
                if endpoint:
                    ip, port = endpoint[0], endpoint[1] if len(endpoint) > 1 else self.http_port
                    if ip:
                        return str(ip).strip(), int(port or self.http_port)
            except Exception:
                pass
        return None, self.http_port

    def _resource_started_callback(self, link):
        def callback(resource):
            tune_incoming_resource(
                resource, self._link_attached_interface(link),
            )
        return callback

    def _setup_link(self, link):
        self.links[link.link_id] = link
        self._optimise_link_mtu(link)
        link.set_link_closed_callback(self._link_closed(link))
        link.set_packet_callback(self._packet_callback(link))
        try:
            link.set_resource_strategy(RNS.Link.ACCEPT_APP)
            link.set_resource_callback(self._resource_accept_callback(link))
            link.set_resource_concluded_callback(self._resource_concluded(link))
            if hasattr(link, "set_resource_started_callback"):
                link.set_resource_started_callback(self._resource_started_callback(link))
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
        if is_hub_peer_hash(peer_hash):
            identity_peer = self.dest_hash_for(self._peer_hash_from_link_identity(link))
            peer_hash = identity_peer if identity_peer and not is_hub_peer_hash(identity_peer) else "unknown"
        self._cache_link_peer(link, peer_hash)
        if is_hub_peer_hash(peer_hash):
            try:
                link.teardown()
            except Exception:
                pass
            print("[messaging] Rejected inbound link — hub group is not a real peer")
            return

        if not self._peer_allowed_by_scope(peer_hash, link=link):
            try:
                link.teardown()
            except Exception:
                pass
            print(
                f"[messaging] Rejected inbound link from {peer_hash[:16]}... "
                "(outside LAN scope)"
            )
            return

        incoming_fam = interface_family(self._link_attached_interface(link))
        expected = self._peer_expected_transport_families(peer_hash)
        if expected and incoming_fam != "serial":
            if incoming_fam in ("udp", "lan", "tcp") and not (expected & {"udp", "lan", "tcp"}):
                try:
                    link.teardown()
                except Exception:
                    pass
                print(
                    f"[messaging] Rejected LAN inbound from {peer_hash[:16]}... "
                    "(serial peer)"
                )
                return
        if incoming_fam == "serial" and peer_hash and peer_hash != "unknown":
            canon = self.dest_hash_for(peer_hash)
            if canon and canon != "unknown":
                prune_lan_path_for_peer(canon)

        if self.active_link and self.active_peer_hash and not is_hub_peer_hash(self.active_peer_hash):
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
                old_score = self._link_path_score(self.active_link)
                new_score = self._link_path_score(link)
                old_healthy = self._link_interface_healthy(self.active_link)
                incoming_fam = interface_family(self._link_attached_interface(link))
                old_fam = interface_family(self._link_attached_interface(self.active_link))
                peer_expected = self._peer_expected_transport_families(peer_hash)
                prefer_serial = (
                    incoming_fam == "serial"
                    and peer_expected == {"serial"}
                ) or (
                    incoming_fam == "serial"
                    and not peer_expected
                    and (
                        self._failover_in_progress
                        or not physical_lan_reachable()
                        or self._peer_has_path_on_family(peer_hash, "serial")
                        or (old_fam in ("udp", "lan") and not old_healthy)
                    )
                )
                if (
                    prefer_serial
                    or new_score > old_score + 8
                    or (not old_healthy and new_score >= old_score)
                ):
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

        if not peer_hash or peer_hash == "unknown":
            resolved = self._peer_hash_from_link_identity(link)
            if resolved and resolved != "unknown":
                peer_hash = resolved
                self._cache_link_peer(link, peer_hash)
        print(f"[messaging] Incoming link established: {link.link_id.hex()[:12]} ({peer_hash[:16]}...)")
        self._last_handoff = False
        self._setup_link(link)
        passive_only = self.is_user_disconnected(peer_hash)
        if passive_only:
            promote = False
        else:
            promote = (
                not self.active_link
                or self.hashes_equivalent(peer_hash, self.active_peer_hash)
            )
        self._notify_link_established(
            link, peer_hash,
            promote_active=promote,
            background=not promote,
            passive=passive_only,
        )
        if not passive_only:
            self._schedule_queue_drain(peer_hash, link=link)

    def _link_closed(self, link):
        def callback(link):
            remote_hash = self.dest_hash_for(self._peer_for_link(link))
            if link.link_id in self.links:
                del self.links[link.link_id]
            self._link_peer_hashes.pop(link.link_id, None)
            if remote_hash and remote_hash != "unknown":
                peer = self.dest_hash_for(remote_hash)
                remaining = self._other_active_links_for_peer(peer, except_link=link)
                if remaining:
                    self.peer_links[peer] = remaining[0]
                    if (
                        self.active_link
                        and self.active_link.link_id == link.link_id
                        and not self._link_handoff
                    ):
                        self._notify_link_established(
                            remaining[0], peer,
                            promote_active=True, background=False,
                        )
                else:
                    self._unlink_peer(peer)
            if not self._link_handoff:
                xfer_peer = self.dest_hash_for(
                    self._session_peer_hash or self.active_peer_hash or remote_hash or ""
                )
                alt_link = self._link_for_peer(xfer_peer) if xfer_peer else None
                if (
                    self._has_active_transfer()
                    and alt_link
                    and alt_link.link_id != link.link_id
                ):
                    self._migrate_pending_files(link.link_id, alt_link.link_id)
                else:
                    self._flush_pending_files_failed(link.link_id)
            closing_active = self.active_link and self.active_link.link_id == link.link_id
            if closing_active and not self._link_handoff:
                lost_peer = self.dest_hash_for(self.active_peer_hash)
                if (
                    self.active_peer_hash
                    and lost_peer
                    and not self.is_user_disconnected(lost_peer)
                ):
                    self._session_peer_hash = self.active_peer_hash
                self.active_link = None
                self.active_peer_hash = None
                if lost_peer and not self.is_user_disconnected(lost_peer):
                    self._last_link_lost_at = time.time()
                session_peer = self.dest_hash_for(self._session_peer_hash or "")
                if session_peer and not self.is_user_disconnected(session_peer):
                    next_link = self._link_for_peer(session_peer)
                    if next_link and next_link.link_id != link.link_id:
                        self.active_link = next_link
                        self.active_peer_hash = session_peer
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
                if remote_hash and not self._peer_allowed_by_scope(remote_hash, link=link):
                    if chat_msg.msg_type not in ("__receipt", "__read_receipt") and chat_msg.msg_type not in CALL_TYPES:
                        print(
                            f"[messaging] Dropped {chat_msg.msg_type} from "
                            f"{remote_hash[:16]}... (outside LAN scope)"
                        )
                    return

                if chat_msg.msg_type == "__receipt":
                    try:
                        receipt = json.loads(chat_msg.content)
                        msg_id = receipt.get("msg_id")
                        status = receipt.get("status", "received")
                        self._pending_sends.pop(msg_id, None)
                        self._remove_queue_entry(msg_id)
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

                if chat_msg.msg_type in CALL_TYPES:
                    self._handle_call_packet(chat_msg, remote_hash, link)
                    return

                if chat_msg.msg_type == MESSAGE_TYPE_LAN_HTTP:
                    self._handle_lan_http_offer(chat_msg, remote_hash)
                    return

                if chat_msg.msg_type == MESSAGE_TYPE_TRANSFER_CANCEL:
                    try:
                        payload = json.loads(chat_msg.content or "{}")
                    except Exception:
                        payload = {}
                    tid = payload.get("transfer_id") or payload.get("msg_id") or chat_msg.msg_id
                    fname = payload.get("file_name") or ""
                    if tid:
                        self._cancelled_transfers.add(tid)
                    self._cancel_incoming_resources(link, transfer_id=tid, file_name=fname)
                    is_sender = (
                        tid in self._active_resources
                        or tid == self._current_transfer_id
                        or tid in self._sent_messages
                    )
                    if is_sender:
                        self.cancel_transfer(
                            transfer_id=tid, file_name=fname, notify_peer=False,
                        )
                        if self.on_transfer_revoked and tid:
                            try:
                                self.on_transfer_revoked(tid, fname)
                            except Exception:
                                pass
                    return

                if not self._hub_message_acceptable(chat_msg, link):
                    print("[hub] Ignored group message (hub transport only)")
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
                chat_msg = self._dequeue_pending_file(link.link_id, resource)
                tid = getattr(chat_msg, "msg_id", None) if chat_msg else None
                if tid and tid in self._cancelled_transfers:
                    fname = (chat_msg.file_name if chat_msg else None) or "file"
                    self._emit_progress(
                        fname, 0, direction="receive", transfer_id=tid,
                        status="cancelled",
                    )
                    return
                if resource.status == RNS.Resource.COMPLETE:

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
                    if not chat_msg:
                        with self._pending_lock:
                            queue = self._pending_files.get(link.link_id, [])
                            chat_msg = queue.pop(0) if queue else None
                    if chat_msg:
                        tid = chat_msg.msg_id
                        status = "cancelled" if tid in self._cancelled_transfers else "failed"
                        self._emit_progress(
                            chat_msg.file_name or "file",
                            0,
                            direction="receive",
                            transfer_id=tid,
                            status=status,
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

    def _emit_progress(self, file_name, progress, total_size=0, speed="", direction="receive",
                       transfer_id=None, status="active", transport=None):
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
        if transport is None and self.active_link:
            transport = self._transfer_transport_label(self.active_link)
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
                    "transport": transport or "",
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

    def _transfer_transport_label(self, link=None):
        link = link or self.active_link
        iface = self._link_attached_interface(link) if link else None
        if is_serial_interface(iface):
            return "serial"
        fam = interface_family(iface) if iface else ""
        if fam in ("udp", "lan", "tcp"):
            return fam or "lan"
        return ""

    def _notify_peer_transfer_cancel(self, transfer_id, file_name=None, link=None):
        """Tell the remote peer to stop receiving an in-flight file."""
        link = link or self.active_link
        if not link or not transfer_id:
            return False
        try:
            if getattr(link, "status", None) != RNS.Link.ACTIVE:
                return False
        except Exception:
            return False
        payload = {"transfer_id": transfer_id, "msg_id": transfer_id}
        if file_name:
            payload["file_name"] = file_name
        meta = ChatMessage(MESSAGE_TYPE_TRANSFER_CANCEL, json.dumps(payload), msg_id=transfer_id)
        try:
            packet = RNS.Packet(link, meta.to_json().encode("utf-8"))
            packet.send()
            print(f"[transfer] Cancel notice sent for {transfer_id[:8]}...")
            return True
        except Exception as exc:
            print(f"[transfer] Cancel notice failed: {exc}")
            return False

    def _cancel_incoming_resources(self, link, transfer_id=None, file_name=None):
        """Abort active incoming RNS resources and drop queued file metadata."""
        if not link:
            return False
        cancelled = False
        fname = file_name or ""
        try:
            for res in list(getattr(link, "incoming_resources", None) or []):
                try:
                    if hasattr(res, "cancel"):
                        res.cancel()
                    elif hasattr(res, "close"):
                        res.close()
                    cancelled = True
                except Exception:
                    pass
        except Exception:
            pass
        with self._pending_lock:
            queue = self._pending_files.get(link.link_id, [])
            kept = []
            for msg in queue:
                match = (
                    (transfer_id and msg.msg_id == transfer_id)
                    or (file_name and msg.file_name == file_name)
                )
                if match:
                    cancelled = True
                    fname = msg.file_name or fname
                    tid = msg.msg_id or transfer_id
                    if tid:
                        self._cancelled_transfers.add(tid)
                    continue
                kept.append(msg)
            self._pending_files[link.link_id] = kept
        if cancelled:
            tid = transfer_id or fname
            transport = self._transfer_transport_label(link)
            self._emit_progress(
                fname or "file",
                0,
                direction="receive",
                transfer_id=transfer_id,
                status="cancelled",
                transport=transport,
            )
            print(f"[transfer] Incoming transfer cancelled: {fname or transfer_id or '?'}")
        return cancelled

    def cancel_transfer(self, transfer_id=None, file_name=None, notify_peer=True):
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
        fname = file_name or ""
        msg = self._sent_messages.get(tid)
        if msg and getattr(msg, "file_name", None):
            fname = msg.file_name
        if not fname:
            for entry in reversed(self.message_queue):
                if entry.get("msg_id") == tid:
                    fname = entry.get("file_name", "")
                    break
        if notify_peer:
            self._notify_peer_transfer_cancel(tid, file_name=fname)
        if cancelled or tid in self._cancelled_transfers:
            transport = self._transfer_transport_label()
            self._emit_progress(
                fname, 0, status="cancelled", direction="send",
                transfer_id=tid, transport=transport,
            )
        if self._current_transfer_id == tid:
            self._current_transfer_id = None
        return cancelled or tid in self._cancelled_transfers

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

    def _drain_queue_after_reconnect(self, peer_hash=None):
        """Send queued messages once a reconnect path is live."""
        peer = self.dest_hash_for(
            peer_hash or self._session_peer_hash or self.active_peer_hash or ""
        )
        if not peer or peer == "unknown" or is_hub_peer_hash(peer):
            return
        if self.is_user_disconnected(peer) or not self._peer_link_active(peer):
            return
        link = self._queue_send_link(peer) or self._link_for_peer(peer)
        self._schedule_queue_drain(peer, link=link, include_files=True)

    def resume_session_peer(self, peer_ip=None, peer_port=None, peer_lookup=None,
                            caller_ip=None, caller_port=8742):
        """Reconnect to the saved session peer after link drop or UI resume."""
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        if not peer or peer == "unknown":
            return False
        if self.is_user_disconnected(peer):
            return False
        if self.active_link and self._peer_link_active(peer):
            return True
        if self._connect_in_progress:
            return False
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
        if self._connect_in_progress:
            return False
        if self._failover_in_progress:
            return False
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        if not peer or peer == "unknown":
            return False
        if self.is_user_disconnected(peer):
            return False
        if self._has_active_transfer():
            if self._peer_link_active(peer):
                return True
            return False
        if self._peer_expected_transport_families(peer) == {"serial"}:
            if configured_serial_enabled(load_settings_interfaces(self.config_dir)):
                if serial_interface_online() is None:
                    return False
        if self._peer_link_active(peer):
            link = self._link_for_peer(peer) or self.active_link
            if link and self._link_interface_healthy(link):
                if not self.active_link or getattr(self.active_link, "link_id", None) != getattr(link, "link_id", None):
                    self._adopt_healthy_peer_link(peer)
                return True
        if now - self._failover_last_attempt < self._failover_cooldown():
            return False
        if not self.active_peer_hash:
            self.active_peer_hash = peer
        if not self._session_peer_hash:
            self._session_peer_hash = peer

        self._failover_last_attempt = now
        self._failover_in_progress = True
        self._transport_reconnect_pending = False
        try:
            families = self._failover_families_to_try(peer, peer_ip=peer_ip)
            print(
                f"[connect] Failover reconnect to {peer[:16]}... ({reason}) "
                f"[{', '.join(families)}]"
            )
            self._teardown_stale_peer_links(peer, handoff=True)
            self._teardown_active_link(preserve_peer=True, handoff=True)
            pause_until = time.time() + 0.3
            while time.time() < pause_until:
                if self._interrupted():
                    return False
                time.sleep(0.05)
            for prefer in families:
                use_ip = peer_ip
                if prefer == "serial":
                    use_ip = None
                elif use_ip:
                    register_udp_peer_ip(use_ip)
                if self._interrupted():
                    return False
                if not self._prepare_failover_path(
                    peer, prefer_family=prefer, peer_ip=use_ip, peer_port=peer_port,
                ):
                    print(f"[connect] {prefer} path not ready — trying next transport")
                    continue
                if prefer == "serial" and self._serial_transport_ready():
                    inbound_wait = SERIAL_INBOUND_FIRST_WAIT_S
                    print(f"[connect] Failover waiting for serial inbound ({inbound_wait}s)...")
                    if self._wait_for_peer_link(peer, timeout_s=inbound_wait):
                        self._adopt_healthy_peer_link(peer)
                        self._drain_queue_after_reconnect(peer)
                        print("[connect] Failover complete (serial inbound)")
                        return True
                elif (
                    prefer in ("udp", "lan")
                    and use_ip
                    and self._lan_transport_ready()
                    and physical_lan_reachable()
                ):
                    inbound_wait = INITIATOR_INBOUND_WAIT_S
                    print(f"[connect] Failover waiting for inbound link on {prefer} ({inbound_wait}s)...")
                    if self._wait_for_peer_link(peer, timeout_s=inbound_wait):
                        self._adopt_healthy_peer_link(peer)
                        self._drain_queue_after_reconnect(peer)
                        print(f"[connect] Failover complete (inbound via {prefer})")
                        return True
                if self._interrupted():
                    return False
                result = self.connect_to(
                    peer,
                    use_ip,
                    peer_port,
                    peer_lookup,
                    caller_ip,
                    caller_port,
                    replace=False,
                    failover=True,
                )
                if result:
                    self._adopt_healthy_peer_link(peer)
                    self._drain_queue_after_reconnect(peer)
                    print(f"[connect] Failover complete via {prefer}")
                    return True
                if prefer == families[-1]:
                    print(f"[connect] Failover connect via {prefer} failed")
                else:
                    print(f"[connect] Failover connect via {prefer} failed — trying next transport")
            return False
        finally:
            self._failover_in_progress = False

    def _interrupted(self):
        return self.shutdown_requested or not self.running

    def connect_to(self, destination_hash_hex, peer_ip=None, peer_port=None, peer_lookup=None,
                   caller_ip=None, caller_port=8742, replace=False, failover=False,
                   respond_to_wake=False, user_initiated=False, prefer_transport=None):
        with self._connect_lock:
            if self._interrupted():
                return False

            self._connect_in_progress = True
            self._connect_user_initiated = bool(user_initiated)
            self._connect_background = bool(respond_to_wake and not user_initiated)
            self._connect_failover = bool(failover)

            try:
                return self._connect_to_locked(
                    destination_hash_hex,
                    peer_ip=peer_ip,
                    peer_port=peer_port,
                    peer_lookup=peer_lookup,
                    caller_ip=caller_ip,
                    caller_port=caller_port,
                    replace=replace,
                    failover=failover,
                    respond_to_wake=respond_to_wake,
                    user_initiated=user_initiated,
                    prefer_transport=prefer_transport,
                )
            finally:
                self._connect_in_progress = False

    def _connect_to_locked(self, destination_hash_hex, peer_ip=None, peer_port=None,
                           peer_lookup=None, caller_ip=None, caller_port=8742,
                           replace=False, failover=False, respond_to_wake=False,
                           user_initiated=False, prefer_transport=None):
            clean = normalize_hash(destination_hash_hex)
            requested_transport = (
                self._normalize_transport(prefer_transport)
                if prefer_transport
                else None
            )
            if len(clean) != 32:
                print(f"[connect] Invalid hash length ({len(clean)} chars, expected 32)")
                return False
            if peer_ip and not self._peer_lan_ip_usable(peer_ip):
                if self._serial_transport_ready() or self._peer_has_path_on_family(clean, "serial"):
                    print(
                        f"[connect] Peer LAN IP {peer_ip} outside scope — "
                        "using serial path"
                    )
                peer_ip = None
            if peer_ip:
                register_udp_peer_ip(peer_ip)

            if user_initiated:
                self.clear_user_disconnected(clean)
                session_hash = self.dest_hash_for(clean) or clean
                self._session_peer_hash = session_hash
                self.active_peer_hash = session_hash
                if requested_transport:
                    self._session_transport = requested_transport
                self._teardown_other_peer_links(session_hash)
                if (
                    peer_ip
                    and physical_lan_reachable()
                    and not respond_to_wake
                    and not self._peer_lan_recently_unreachable(peer_ip)
                ):
                    print(f"[connect] Waking LAN peer at {peer_ip}:{peer_port or 8742}")
                    self._wake_peer(
                        peer_ip, peer_port, self.my_dest_hash or "",
                        caller_ip=caller_ip, caller_port=caller_port,
                    )
                    pruned = self._teardown_stale_peer_links(clean, handoff=True)
                    if pruned:
                        print(f"[connect] Closed {pruned} stale link(s) for {clean[:16]}...")
                pruned = self._teardown_mismatched_links(clean)
                if pruned:
                    print(f"[connect] Closed {pruned} stale link(s) for {clean[:16]}...")
            elif respond_to_wake and self.is_user_disconnected(clean):
                print(
                    f"[connect] Passive mode — not reverse-connecting to "
                    f"{clean[:16]}... (user disconnected)"
                )
                inbound = self._find_active_link_for_peer(clean)
                if inbound:
                    self._notify_link_established(
                        inbound, clean, promote_active=False, background=True, passive=True,
                    )
                return bool(inbound)

            old_link = None
            if self.active_link and self.active_peer_hash and self.hashes_equivalent(clean, self.active_peer_hash):
                link_ok = self._link_interface_healthy(self.active_link) and self._peer_has_path(clean)
                active_transport = self._transport_from_link(self.active_link)
                transport_ok = (
                    not requested_transport
                    or active_transport == requested_transport
                )
                if not replace:
                    if link_ok and transport_ok:
                        print(
                            f"[connect] Already connected to {self.active_peer_hash[:16]}..."
                            f" ({active_transport})"
                        )
                        return self._finish_connect(
                            clean, link=self.active_link, transport=requested_transport,
                        )
                    if link_ok and not transport_ok:
                        print(
                            f"[connect] Active {active_transport} link — "
                            f"opening separate {requested_transport} session..."
                        )
                    elif not link_ok:
                        print(f"[connect] Stale link to {self.active_peer_hash[:16]}... — reconnecting")
                        self._teardown_active_link(preserve_peer=True, handoff=True)
                elif self._link_path_score(self.active_link) >= 90 and link_ok and transport_ok:
                    return self._finish_connect(
                        clean, link=self.active_link, transport=requested_transport,
                    )
                else:
                    old_link = self.active_link
                    self._teardown_active_link(preserve_peer=True, handoff=True)
                    print(f"[connect] Replacing link to {self.active_peer_hash[:16]} for better path...")
            elif self._peer_link_active(clean, transport=requested_transport):
                usable, adopt = self._peer_link_usable(clean, transport=requested_transport)
                if usable:
                    print(
                        f"[connect] Already linked to {clean[:16]}... "
                        f"({self._transport_from_link(adopt) if adopt else requested_transport or 'active'})"
                    )
                    if user_initiated and adopt:
                        self._notify_link_established(
                            adopt, clean, promote_active=True, background=False,
                        )
                    return self._finish_connect(
                        clean, link=adopt, transport=requested_transport,
                    )
                pruned = self._teardown_stale_peer_links(clean, handoff=True)
                if pruned:
                    print(f"[connect] Closed {pruned} stale link(s) for {clean[:16]}...")

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

            if self._hub_transport_active() and not self._peer_uses_hub_transport(dest_hex):
                _, path_iface = peer_path_entry(dest_hex)
                if path_iface and self._link_is_hub_transport(path_iface):
                    clear_peer_path(dest_hex)

            my_hash = normalize_hash(self.my_dest_hash or dest_hex)
            inbound = self._find_active_link_for_peer(dest_hex, clean)
            if inbound and self._link_transport_matches(inbound, requested_transport):
                self._cache_link_peer(inbound, dest_hex)
                self._notify_link_established(
                    inbound, dest_hex, promote_active=True, background=False,
                )
                print(f"[connect] Adopted inbound link to {dest_hex[:16]}...")
                return self._finish_connect(
                    dest_hex, link=inbound, transport=requested_transport,
                )
            usable, adopt = self._peer_link_usable(
                dest_hex, clean, transport=requested_transport,
            )
            if usable:
                print(f"[connect] Already linked to {dest_hex[:16]}... (inbound)")
                return self._finish_connect(
                    dest_hex, link=adopt, transport=requested_transport,
                )
            if adopt:
                pruned = self._teardown_stale_peer_links(dest_hex, handoff=True)
                if pruned:
                    print(f"[connect] Closed {pruned} stale link(s) for {dest_hex[:16]}...")

            physical_lan = physical_lan_reachable()
            peer_lan_down = bool(peer_ip and self._peer_lan_recently_unreachable(peer_ip))
            if peer_lan_down:
                peer_ip = None
                clear_paths_on_family("udp")
            self._ensure_runtime_serial_transport()
            lan_ready = self._lan_transport_ready() and physical_lan and not peer_lan_down
            serial_ready = self._serial_transport_ready()
            prefer_serial = self._should_prefer_serial_connect(
                dest_hex, peer_ip=peer_ip, peer_lookup=peer_lookup,
            )
            if requested_transport == "serial":
                prefer_serial = True
                peer_ip = None
            elif requested_transport == "lan":
                prefer_serial = False
            serial_only = serial_ready and (prefer_serial or not lan_ready or peer_lan_down)
            prune_stale_lan_paths()
            bridged = prune_bridged_lan_paths()
            if bridged:
                print(f"[connect] Cleared {bridged} bridged LAN path(s)")
            if prefer_serial:
                clear_peer_path_unless_family(dest_hex, "serial")
                peer_ip = None

            serial_only_peer = (
                prefer_serial
                or serial_only
                or self._peer_expected_transport_families(dest_hex) == {"serial"}
            )
            if serial_ready and serial_only_peer:
                prime_timeout = 12.0 if not physical_lan else 8.0
                if self._connect_serial_peer(
                    destination, dest_hex, clean, old_link=old_link,
                    prime_timeout=prime_timeout,
                ):
                    adopt = (
                        self._link_for_peer(dest_hex, transport="serial")
                        or self.active_link
                    )
                    return self._finish_connect(
                        dest_hex, link=adopt, transport="serial",
                    )
                print("[connect] Peer not reachable (serial)")
                return False

            if self._tcp_connect_ready(dest_hex, peer_ip, peer_lan_down, prefer_serial=prefer_serial):
                if peer_ip:
                    self._prime_tcp_path(dest_hex, peer_ip=peer_ip, timeout_s=2.5)
                print(f"[connect] LAN/TCP path ready — quick connect ({QUICK_OUTBOUND_TIMEOUT_S}s)")
                if self._establish_outbound_link(
                    destination, dest_hex, clean, old_link=old_link,
                    timeout_s=QUICK_OUTBOUND_TIMEOUT_S,
                ):
                    return self._finish_connect(dest_hex)
                if self._peer_link_active(dest_hex, clean):
                    adopt = self._link_for_peer(dest_hex) or self._find_active_link_for_peer(dest_hex, clean)
                    return self._finish_connect(dest_hex, link=adopt)

            if self._udp_connect_ready(dest_hex, peer_ip, peer_lan_down, prefer_serial=prefer_serial):
                if peer_ip:
                    self._prime_udp_path(dest_hex, peer_ip=peer_ip, timeout_s=2.5)
                print(f"[connect] LAN/UDP path ready — quick connect ({QUICK_OUTBOUND_TIMEOUT_S}s)")
                if self._establish_outbound_link(
                    destination, dest_hex, clean, old_link=old_link,
                    timeout_s=QUICK_OUTBOUND_TIMEOUT_S,
                ):
                    return self._finish_connect(dest_hex)
                if self._peer_link_active(dest_hex, clean):
                    adopt = self._link_for_peer(dest_hex) or self._find_active_link_for_peer(dest_hex, clean)
                    return self._finish_connect(dest_hex, link=adopt)

            elif peer_ip and not respond_to_wake and lan_ready:
                self._prime_lan_path(dest_hex, peer_ip=peer_ip, timeout_s=2.5)
                if self._peer_has_path(dest_hex):
                    print(f"[connect] Path known — quick outbound attempt ({QUICK_OUTBOUND_TIMEOUT_S}s)")
                    if self._establish_outbound_link(
                        destination, dest_hex, clean, old_link=old_link,
                        timeout_s=QUICK_OUTBOUND_TIMEOUT_S,
                    ):
                        adopt = self._link_for_peer(dest_hex) or self.active_link
                        return self._finish_connect(dest_hex, link=adopt)
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
                    adopt = self._link_for_peer(dest_hex) or self.active_link
                    return self._finish_connect(dest_hex, link=adopt)
                print(f"[connect] Waiting for peer outbound link ({inbound_wait}s)...")
                if self._wait_for_peer_link(dest_hex, alt_hex=clean, timeout_s=inbound_wait):
                    print("[connect] Link established (inbound after wake)")
                    adopt = self._link_for_peer(dest_hex) or self.active_link
                    return self._finish_connect(dest_hex, link=adopt)
                print("[connect] Peer did not connect back — trying outbound fallback...")
            elif serial_ready and peer_ip and not lan_ready:
                print("[connect] LAN unreachable — using serial only (no HTTP wake)")
                peer_ip = None
                self._prime_serial_path(dest_hex)
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
                self._prime_lan_path(dest_hex, peer_ip=peer_ip)
            else:
                request_paths_for_hash(dest_hex)
            if is_android() and not peer_ip and not serial_ready:
                print("[connect] Android: no peer IP — connect from Discovered list or add contact with LAN IP")
            if serial_only or (serial_ready and not lan_ready):
                connect_timeout = SERIAL_LINK_CONNECT_TIMEOUT_S
            elif failover:
                connect_timeout = FAILOVER_CONNECT_TIMEOUT_S
            elif is_android():
                connect_timeout = ANDROID_LINK_CONNECT_TIMEOUT_S
            else:
                connect_timeout = LINK_CONNECT_TIMEOUT_S
            path_hint = "serial" if (serial_only or (serial_ready and not lan_ready)) else "auto"
            print(f"[connect] Connecting to {dest_hex[:16]}... ({path_hint}, timeout {connect_timeout}s)")

            if self._establish_outbound_link(
                destination, dest_hex, clean, old_link=old_link,
                timeout_s=connect_timeout,
            ):
                adopt = self._link_for_peer(dest_hex) or self.active_link
                return self._finish_connect(dest_hex, link=adopt)

            if self._peer_link_active(dest_hex, clean):
                adopt = self._adopt_healthy_peer_link(dest_hex)
                print("[connect] Link established (inbound after outbound attempt)")
                return self._finish_connect(dest_hex, link=adopt)

            if peer_ip and lan_ready and physical_lan:
                reverse_wait = ANDROID_REVERSE_CONNECT_WAIT_S if is_android() else REVERSE_CONNECT_WAIT_S
                print(f"[connect] Outbound timed out — waiting for reverse connect ({reverse_wait}s)...")
                if not respond_to_wake:
                    self._wake_peer(
                        peer_ip, peer_port, my_hash,
                        caller_ip=caller_ip, caller_port=caller_port,
                    )
                if self._wait_for_reverse_link(dest_hex, alt_hex=clean, timeout_s=reverse_wait):
                    print("[connect] Reverse connect established")
                    adopt = self._link_for_peer(dest_hex) or self.active_link
                    return self._finish_connect(dest_hex, link=adopt)

            if (
                serial_ready
                and not serial_only
                and (peer_lan_down or not physical_lan)
                and not self._peer_link_active(dest_hex, clean)
            ):
                print("[connect] Retrying over serial after LAN path failed...")
                scrub_peer_path(dest_hex)
                request_paths_for_hash(dest_hex, family="serial")
                self._prime_serial_path(dest_hex, timeout_s=14.0)
                if self._establish_outbound_link(
                    destination, dest_hex, clean, old_link=old_link,
                    timeout_s=SERIAL_LINK_CONNECT_TIMEOUT_S,
                ):
                    adopt = self._link_for_peer(dest_hex) or self.active_link
                    return self._finish_connect(dest_hex, link=adopt)
                if self._wait_for_peer_link(
                    dest_hex, alt_hex=clean, timeout_s=REVERSE_CONNECT_WAIT_S,
                ):
                    adopt = self._adopt_healthy_peer_link(dest_hex)
                    print("[connect] Link established (serial inbound after LAN failure)")
                    return self._finish_connect(dest_hex, link=adopt)

            print("[connect] Peer not reachable")
            return False

    def send_hub_message(self, text, receipt_callback=None, msg_id=None,
                       hub_server_hash=None, hub_server_mode=False):
        msg = ChatMessage(MESSAGE_TYPE_TEXT, text, msg_id=msg_id)
        msg.hub_group = True
        data = msg.to_json().encode("utf-8")
        targets = self._hub_send_targets(
            hub_server_hash=hub_server_hash,
            hub_server_mode=hub_server_mode,
        )
        sent = False
        for peer in targets:
            if not peer or is_hub_peer_hash(peer):
                continue
            link = self._link_for_peer(peer)
            if not link:
                continue
            try:
                mtu = getattr(link, "mtu", 500)
                if len(data) > mtu - 50:
                    if not self._send_long_text(msg, text, data, receipt_callback, link):
                        print(f"[hub] send failed to {peer[:16]}: long text transfer failed")
                        continue
                else:
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
        for peer in self._hub_tcp_linked_peers():
            if is_hub_peer_hash(peer) or self.hashes_equivalent(peer, sender_hash):
                continue
            link = self._link_for_peer(peer)
            if not link:
                continue
            try:
                RNS.Packet(link, data).send()
            except Exception as e:
                print(f"[hub] relay failed to {peer[:16]}: {e}")

    def peer_send_ready(self, target_peer=None):
        peer = self.dest_hash_for(
            target_peer or self.active_peer_hash or self._session_peer_hash or ""
        )
        if not peer or peer == "unknown":
            return False
        if not self._peer_link_active(peer):
            return False
        link = self._queue_send_link(peer)
        return bool(
            link
            and self._link_matches_peer(link, peer)
            and self._link_interface_healthy(link)
        )

    def send_message(self, text, receipt_callback=None, msg_id=None, target_peer=None,
                     link=None):
        peer = self.dest_hash_for(
            target_peer or self.active_peer_hash or self._session_peer_hash or ""
        )
        if not peer or peer == "unknown":
            print("[messaging] send_message: no target peer")
            return False
        if not self._peer_link_active(peer):
            print(f"[messaging] send_message: no active link to {peer[:16]}")
            return False
        link = self._queue_send_link(peer, link_hint=link)
        if not link or not self._link_matches_peer(link, peer):
            print(f"[messaging] send_message: no transport-safe link to {peer[:16]}")
            return False
        remote = self._link_remote_peer_hash(link)
        if remote and not self.hashes_equivalent(remote, peer):
            print(
                f"[messaging] send_message: link remote {remote[:16]} "
                f"≠ target {peer[:16]} — blocked"
            )
            return False
        if not self._link_interface_healthy(link):
            alt = self._queue_send_link(peer)
            if (
                alt
                and alt.link_id != link.link_id
                and self._link_matches_peer(alt, peer)
                and self._link_interface_healthy(alt)
            ):
                link = alt
            else:
                print(f"[messaging] send_message: link transport offline for {peer[:16]}")
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

    def _watch_lan_http_send(self, transfer_id, fname, fsize):
        from chatxz.core.lan_transfer import get_offer_state

        deadline = time.time() + max(7200, fsize / (200 * 1024))
        while time.time() < deadline:
            if transfer_id in self._cancelled_transfers:
                remove_offer(transfer_id)
                self._emit_progress(fname, 0, fsize, status="cancelled", direction="send", transfer_id=transfer_id)
                if self._current_transfer_id == transfer_id:
                    self._current_transfer_id = None
                return
            offer = get_offer_state(transfer_id)
            if offer is None:
                self._emit_progress(fname, 100, fsize, status="complete", direction="send", transfer_id=transfer_id)
                if self._current_transfer_id == transfer_id:
                    self._current_transfer_id = None
                return
            sent = int(offer.get("bytes_sent") or 0)
            pct = int((sent / fsize) * 100) if fsize else 0
            speed = self._calc_transfer_speed(transfer_id, sent)
            self._emit_progress(fname, pct, fsize, speed=speed, direction="send", transfer_id=transfer_id)
            time.sleep(0.2)

    def _send_file_lan_http(self, file_path, msg_type, fname, fsize, transfer_id, link, peer, progress_callback):
        peer_ip, _peer_port = self._peer_endpoint(peer)
        host_ip = lan_ip()
        if not peer_ip or not host_ip:
            return None
        token = register_offer(
            transfer_id, file_path, peer,
            host=host_ip, port=self.http_port,
        )
        offer = {
            "transfer_id": transfer_id,
            "token": token,
            "host": host_ip,
            "port": self.http_port,
            "file_name": fname,
            "file_size": fsize,
            "msg_type": msg_type,
        }
        meta = ChatMessage(
            MESSAGE_TYPE_LAN_HTTP,
            json.dumps(offer),
            file_name=fname,
            file_size=fsize,
            msg_id=transfer_id,
        )
        packet = RNS.Packet(link, meta.to_json().encode("utf-8"))
        packet.send()
        print(
            f"[transfer] LAN HTTP offer {fname} ({fsize} bytes) "
            f"http://{host_ip}:{self.http_port}/api/lan-transfer/{transfer_id}"
        )
        threading.Thread(
            target=self._watch_lan_http_send,
            args=(transfer_id, fname, fsize),
            name=f"lan-http-send-{transfer_id[:8]}",
            daemon=True,
        ).start()
        return meta

    def _handle_lan_http_offer(self, chat_msg, remote_hash):
        threading.Thread(
            target=self._download_lan_http_offer,
            args=(chat_msg, remote_hash),
            name=f"lan-http-rx-{chat_msg.msg_id[:8]}",
            daemon=True,
        ).start()

    def _download_lan_http_offer(self, chat_msg, remote_hash):
        from chatxz.utils.helpers import safe_basename, safe_path_under

        try:
            offer = json.loads(chat_msg.content or "{}")
        except Exception as exc:
            print(f"[transfer] Invalid LAN HTTP offer: {exc}")
            return
        host = (offer.get("host") or "").strip()
        port = int(offer.get("port") or self.http_port)
        transfer_id = offer.get("transfer_id") or chat_msg.msg_id
        token = offer.get("token") or ""
        fname = safe_basename(offer.get("file_name") or chat_msg.file_name or f"file_{int(time.time())}")
        fsize = int(offer.get("file_size") or chat_msg.file_size or 0)
        if not host or not token:
            print("[transfer] LAN HTTP offer missing host/token")
            return
        url = f"http://{host}:{port}/api/lan-transfer/{transfer_id}?token={token}"
        os.makedirs(self.receive_dir, exist_ok=True)
        save_path = safe_path_under(self.receive_dir, fname)
        if not save_path:
            print(f"[transfer] Rejected unsafe LAN HTTP filename: {fname!r}")
            return
        self._emit_progress(fname, 0, fsize, direction="receive", transfer_id=transfer_id, status="active")
        received = 0
        try:
            req = urlrequest.Request(url, method="GET")
            with urlrequest.urlopen(req, timeout=max(60, fsize // (512 * 1024))) as resp:
                with open(save_path, "wb") as out:
                    while True:
                        chunk = resp.read(LAN_HTTP_CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        received += len(chunk)
                        pct = int((received / fsize) * 100) if fsize else 0
                        speed = self._calc_transfer_speed(transfer_id, received)
                        self._emit_progress(
                            fname, pct, fsize, speed=speed,
                            direction="receive", transfer_id=transfer_id,
                        )
            print(f"[transfer] LAN HTTP saved {fname} -> {save_path} ({received} bytes)")
            self._emit_progress(fname, 100, fsize, direction="receive", transfer_id=transfer_id, status="complete")
            if self.on_message:
                done = ChatMessage(
                    offer.get("msg_type", MESSAGE_TYPE_FILE),
                    save_path,
                    sender=remote_hash,
                    file_name=fname,
                    file_size=received or fsize,
                    msg_id=transfer_id,
                )
                self.on_message(done, remote_hash)
        except Exception as exc:
            print(f"[transfer] LAN HTTP download failed: {exc}")
            self._emit_progress(fname, 0, fsize, direction="receive", transfer_id=transfer_id, status="failed")
            try:
                if os.path.isfile(save_path) and os.path.getsize(save_path) == 0:
                    os.remove(save_path)
            except OSError:
                pass

    def send_file(self, file_path, msg_type=MESSAGE_TYPE_FILE, progress_callback=None,
                  transfer_id=None, target_peer=None, link=None):
        peer = self.dest_hash_for(target_peer or self.active_peer_hash or "")
        link = link or self._best_transfer_link(peer) or self._outgoing_link(peer)
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
                if (
                    self.lan_transfer_enabled
                    and fsize >= LAN_HTTP_MIN_BYTES
                    and physical_lan_reachable()
                    and not self._hub_transport_active()
                ):
                    lan_msg = self._send_file_lan_http(
                        file_path, msg_type, fname, fsize, transfer_id, link, peer, progress_callback,
                    )
                    if lan_msg:
                        self._sent_messages[chat_msg.msg_id] = chat_msg
                        return chat_msg

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
                xfer_link = link or self._outgoing_link(peer)
                xfer_iface = self._link_attached_interface(xfer_link)
                xfer_fam = interface_family(xfer_iface)
                fast_path = xfer_fam in ("tcp", "lan", "udp")
                serial_path = is_serial_interface(xfer_iface)
                compress = (
                    not serial_path
                    and msg_type not in (MESSAGE_TYPE_IMAGE, MESSAGE_TYPE_VIDEO)
                    and fsize > 65536
                    and ext not in _NO_COMPRESS_SUFFIXES
                    and not fast_path
                )
                timeout_s = None
                if serial_path:
                    from chatxz.core.serial_transfer import serial_transfer_timeout_s, serial_baud_from_interface
                    timeout_s = serial_transfer_timeout_s(
                        fsize, serial_baud_from_interface(xfer_iface),
                    )
                resource = RNS.Resource(
                    f, link,
                    callback=self._resource_send_callback(fname, transfer_id, fsize),
                    progress_callback=wrapped_progress,
                    auto_compress=compress,
                    timeout=timeout_s,
                )
                tune_outgoing_resource(resource, xfer_iface)
                resource_holder["resource"] = resource
                self._active_resources[transfer_id] = resource
                mode = "serial (window=2)" if serial_path else (xfer_fam or "unknown")
                print(f"[messaging] Sent file: {fname} ({fsize} bytes) via {mode}")
                self._sent_messages[chat_msg.msg_id] = chat_msg
                return chat_msg
            except Exception as e:
                print(f"[messaging] File send failed: {e}")
                self._emit_progress(fname, 0, fsize, status="failed", direction="send", transfer_id=transfer_id)
                self._cleanup_transfer(transfer_id)
                return False

    def _resource_send_callback(self, fname, transfer_id=None, fsize=0):
        def callback(resource):
            was_cancelled = (
                self.shutdown_requested or transfer_id in self._cancelled_transfers
            )
            self._cleanup_transfer(transfer_id)
            if was_cancelled:
                self._cancelled_transfers.discard(transfer_id)
                print(f"[messaging] File transfer cancelled: {fname}")
                self._emit_progress(
                    fname, 0, fsize, status="cancelled", direction="send",
                    transfer_id=transfer_id,
                )
                if self.on_transfer_revoked and transfer_id:
                    try:
                        self.on_transfer_revoked(transfer_id, fname)
                    except Exception:
                        pass
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

    def _iter_call_links_for_peer(self, peer_hash, transport=None):
        """All ACTIVE links to peer — includes paths with briefly unhealthy TCP."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return []
        seen = set()
        ordered = []

        def add(link):
            if not link:
                return
            lid = getattr(link, "link_id", None) or id(link)
            if lid in seen:
                return
            seen.add(lid)
            ordered.append(link)

        add(self._link_for_peer(peer, transport=transport))
        add(self._find_active_link_for_peer(peer))
        if self.active_link:
            ap = self._peer_hash_from_link_identity(self.active_link)
            if ap and self.hashes_equivalent(ap, peer):
                add(self.active_link)
        for link in list(self.links.values()):
            try:
                if link.status != RNS.Link.ACTIVE:
                    continue
            except Exception:
                continue
            lp = self._peer_hash_from_link_identity(link)
            if lp and self.hashes_equivalent(lp, peer):
                add(link)
        return ordered

    def _peer_has_healthy_call_link(self, peer_hash, transport=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return False
        for link in self._iter_call_links_for_peer(peer, transport):
            try:
                if link.status != RNS.Link.ACTIVE:
                    continue
            except Exception:
                continue
            if self._link_interface_healthy(link):
                return True
        return False

    def _call_link_for_peer(self, peer_hash, transport=None, *, prefer_healthy=False):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        best = None
        best_score = -1
        degraded = None
        degraded_score = -1
        for link in self._iter_call_links_for_peer(peer, transport):
            try:
                if link.status != RNS.Link.ACTIVE:
                    continue
            except Exception:
                continue
            iface = self._link_attached_interface(link)
            healthy = self._link_interface_healthy(link)
            score = self._interface_path_score(iface) if healthy else 10
            if healthy:
                if score > best_score:
                    best_score = score
                    best = link
            elif score > degraded_score:
                degraded_score = score
                degraded = link
        if best:
            return best
        if prefer_healthy:
            return None
        return degraded

    def _send_call_end_packets(self, peer_hash, call_id, transport=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return False
        payload = {"call_id": (call_id or "").strip()}
        sent = False
        seen = set()
        for link in self._iter_call_links_for_peer(peer, transport):
            lid = getattr(link, "link_id", None) or id(link)
            if lid in seen:
                continue
            seen.add(lid)
            try:
                if link.status != RNS.Link.ACTIVE:
                    continue
            except Exception:
                continue
            msg = ChatMessage(CALL_END, json.dumps(payload))
            data = msg.to_json().encode("utf-8")
            mtu = max(400, int(getattr(link, "mtu", 500) or 500) - 48)
            if len(data) > mtu:
                continue
            try:
                RNS.Packet(link, data).send()
                sent = True
            except Exception:
                continue
        if sent:
            return True
        return self._send_call_packet(peer, CALL_END, payload, transport)

    def _emit_call_event(self, event, peer_hash, payload=None):
        if not self.on_call_event:
            return
        try:
            self.on_call_event(event, peer_hash, payload or {})
        except Exception as e:
            print(f"[call] on_call_event error: {e}")

    def _send_call_packet(self, peer_hash, msg_type, payload, transport=None):
        peer = self.dest_hash_for(peer_hash)
        link = self._call_link_for_peer(peer, transport)
        if not link:
            if msg_type != CALL_AUDIO:
                print(f"[call] No link to {peer[:16] if peer else '?'}...")
            return False
        msg = ChatMessage(msg_type, json.dumps(payload))
        data = msg.to_json().encode("utf-8")
        mtu = max(400, int(getattr(link, "mtu", 500) or 500) - 48)
        if len(data) > mtu:
            if msg_type == CALL_AUDIO:
                print(f"[call] Audio packet too large ({len(data)} > {mtu}), dropped")
            else:
                print(f"[call] Packet too large ({len(data)} > {mtu})")
            return False
        try:
            RNS.Packet(link, data).send()
            return True
        except Exception as e:
            print(f"[call] Send failed: {e}")
            return False

    def _call_id_matches(self, call_id, peer_hash=None):
        cid = (call_id or "").strip()
        active = (self.voice_call.call_id or "").strip()
        if not active:
            return True
        if not cid:
            if peer_hash is not None:
                return self._call_peer_matches(peer_hash)
            return True
        return cid == active

    def _call_glare_we_win(self, their_call_id):
        """True if our outgoing call_id wins simultaneous-invite glare."""
        ours = (self.voice_call.call_id or "").strip()
        theirs = (their_call_id or "").strip()
        if not ours or not theirs:
            return False
        return ours < theirs

    def _call_peer_matches(self, peer_hash):
        peer = self.dest_hash_for(peer_hash) or peer_hash
        active = self.voice_call.peer_hash
        if not active or not peer:
            return True
        return self.hashes_equivalent(peer, active)

    def _call_reset_if_stale(self):
        if self.voice_call.is_stale():
            print(f"[call] Clearing stale call state ({self.voice_call.state})")
            self.voice_call.reset()

    def _handle_call_packet(self, chat_msg, remote_hash, link):
        payload = parse_call_payload(chat_msg.content)
        peer = self.dest_hash_for(remote_hash) or remote_hash
        msg_type = chat_msg.msg_type
        call_id = (payload.get("call_id") or "").strip()

        if msg_type == CALL_INVITE:
            self._call_reset_if_stale()
            if self.voice_call.is_busy():
                if (
                    self.voice_call.state == STATE_OUTGOING
                    and self._call_peer_matches(peer)
                ):
                    if self._call_glare_we_win(call_id):
                        self._send_call_packet(
                            peer,
                            CALL_REJECT,
                            {"call_id": call_id, "reason": "glare"},
                            payload.get("transport"),
                        )
                        print(f"[call] Glare won — kept outgoing, rejected {peer[:16]}...")
                        return
                    print(f"[call] Glare lost — auto-accepting invite from {peer[:16]}...")
                    self.voice_call.reset()
                    transport = (payload.get("transport") or "lan").strip().lower()
                    self.voice_call.begin_incoming(call_id, peer, transport)
                    self.voice_call.activate(call_id)
                    self._reset_call_audio_counters()
                    self._send_call_packet(
                        peer,
                        CALL_ACCEPT,
                        {"call_id": call_id},
                        transport,
                    )
                    self._emit_call_event("accepted", peer, {
                        "call_id": call_id,
                        "transport": transport,
                        "caller_name": payload.get("caller_name") or "",
                        "glare": True,
                    })
                    print(f"[call] Glare auto-accepted {peer[:16]}... ({call_id})")
                    return
                elif (
                    self.voice_call.state == STATE_ACTIVE
                    and self._call_peer_matches(peer)
                ):
                    print(f"[call] Ignoring duplicate invite during active call")
                    return
                else:
                    self._send_call_packet(
                        peer,
                        CALL_REJECT,
                        {"call_id": call_id, "reason": "busy"},
                        payload.get("transport"),
                    )
                    print(f"[call] Busy — rejected invite from {peer[:16]}...")
                    return
            transport = (payload.get("transport") or "lan").strip().lower()
            self.voice_call.begin_incoming(call_id, peer, transport)
            self._emit_call_event("incoming", peer, {
                "call_id": call_id,
                "transport": transport,
                "caller_name": payload.get("caller_name") or "",
            })
            print(f"[call] Incoming from {peer[:16]}... ({call_id})")
            return

        if msg_type == CALL_ACCEPT:
            if self.voice_call.state == STATE_OUTGOING and (
                not call_id or call_id == self.voice_call.call_id
            ):
                self.voice_call.activate(call_id)
                self._reset_call_audio_counters()
                self._emit_call_event("accepted", peer, {"call_id": self.voice_call.call_id})
                print(f"[call] Accepted by {peer[:16]}...")
            return

        if msg_type == CALL_REJECT:
            if self.voice_call.state == STATE_IDLE:
                return
            if not self._call_peer_matches(peer):
                return
            if not self._call_id_matches(call_id, peer):
                return
            reason = payload.get("reason") or ""
            active_cid = self.voice_call.call_id
            self.voice_call.reset()
            self._emit_call_event("rejected", peer, {
                "call_id": call_id or active_cid,
                "reason": reason,
            })
            print(f"[call] Rejected by {peer[:16]}... ({reason or 'declined'})")
            return

        if msg_type == CALL_END:
            if self.voice_call.state == STATE_IDLE:
                return
            if not self._call_peer_matches(peer):
                return
            if not self._call_id_matches(call_id, peer):
                return
            active_cid = self.voice_call.call_id
            self.voice_call.reset()
            self._reset_call_audio_counters()
            self._call_send_link_fails = 0
            self._emit_call_event("ended", peer, {"call_id": call_id or active_cid})
            print(f"[call] Remote hang-up from {peer[:16]}...")
            return

        if msg_type == CALL_AUDIO:
            if getattr(self, "shutdown_requested", False):
                return
            if self.voice_call.state != STATE_ACTIVE:
                return
            if call_id and call_id != self.voice_call.call_id:
                return
            codec = (payload.get("codec") or "").strip()
            if codec and "opus" not in codec.lower():
                return
            recv = int(getattr(self, "_call_audio_recv", 0) or 0) + 1
            self._call_audio_recv = recv
            self._call_last_audio_in_at = time.monotonic()
            self._call_remote_silent_since = None
            if recv <= 2 or recv % 40 == 0:
                b64_len = len(payload.get("data") or "")
                print(f"[call] Audio in #{recv} ({b64_len} b64) ← {peer[:16]}...")
            self._emit_call_event("audio", peer, payload)
            return

    def _reset_call_audio_counters(self):
        self._call_audio_sent = 0
        self._call_audio_recv = 0
        self._call_send_link_fails = 0
        self._call_link_fail_since = None
        self._call_last_audio_in_at = None
        self._call_remote_silent_since = None

    def call_invite(self, peer_hash, transport="lan"):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        self._call_reset_if_stale()
        if not self._peer_link_active(peer):
            print(f"[call] Invite blocked — no active link to {peer[:16]}...")
            return None
        if self.voice_call.is_busy():
            if (
                self.voice_call.state == STATE_OUTGOING
                and self._call_peer_matches(peer)
            ):
                return self.voice_call.call_id
            return None
        if not self._call_link_for_peer(peer, transport):
            return None
        call_id = self.voice_call.begin_outgoing(peer, transport)
        ok = self._send_call_packet(peer, CALL_INVITE, {
            "call_id": call_id,
            "transport": self.voice_call.transport,
            "caller_name": self.display_name or "",
        }, transport)
        if not ok:
            self.voice_call.reset()
            return None
        self._emit_call_event("outgoing", peer, {
            "call_id": call_id,
            "transport": self.voice_call.transport,
        })
        print(f"[call] Outgoing to {peer[:16]}... ({call_id})")
        return call_id

    def call_accept(self, call_id=None):
        if self.voice_call.state != STATE_INCOMING:
            return False
        peer = self.voice_call.peer_hash
        cid = call_id or self.voice_call.call_id
        if not self._send_call_packet(peer, CALL_ACCEPT, {"call_id": cid}, self.voice_call.transport):
            return False
        self.voice_call.activate(cid)
        self._reset_call_audio_counters()
        self._emit_call_event("accepted", peer, {"call_id": cid})
        print(f"[call] Accepted {peer[:16]}...")
        return True

    def call_reject(self, call_id=None, reason=""):
        if self.voice_call.state not in (STATE_INCOMING, STATE_OUTGOING):
            return False
        peer = self.voice_call.peer_hash
        cid = call_id or self.voice_call.call_id
        self._send_call_packet(peer, CALL_REJECT, {
            "call_id": cid,
            "reason": reason or "",
        }, self.voice_call.transport)
        self.voice_call.reset()
        self._emit_call_event("rejected", peer, {"call_id": cid, "reason": reason})
        return True

    def call_end(self, call_id=None, peer_hash=None, transport=None):
        """End local call and notify remote peer with CALL_END."""
        if getattr(self, "_call_ending", False):
            return self.voice_call.state == STATE_IDLE
        peer = (peer_hash or self.voice_call.peer_hash or "").strip()
        cid = (call_id or self.voice_call.call_id or "").strip()
        via = (transport or self.voice_call.transport or "lan").strip().lower() or "lan"
        was_active = self.voice_call.state != STATE_IDLE
        if self.voice_call.state == STATE_IDLE:
            if peer:
                self._send_call_end_packets(peer, cid, via)
                self._emit_call_event("ended", peer, {"call_id": cid})
                print(f"[call] Ended remote notify ({cid or 'no-id'})")
                return True
            return False
        self._call_ending = True
        try:
            peer = peer or self.voice_call.peer_hash
            cid = cid or self.voice_call.call_id
            via = via or self.voice_call.transport
            self.voice_call.reset()
            self._reset_call_audio_counters()
            self._call_send_link_fails = 0
            if peer:
                self._send_call_end_packets(peer, cid, via)
            self._emit_call_event("ended", peer, {"call_id": cid})
            print(f"[call] Ended ({cid})" if was_active else f"[call] Ended remote notify ({cid or 'no-id'})")
            return True
        finally:
            self._call_ending = False

    def _maybe_end_call_remote_gone(self, peer, transport=None):
        """End active call when peer stopped sending and links are unhealthy."""
        if self.voice_call.state != STATE_ACTIVE:
            return False
        if not self._call_peer_matches(peer):
            return False
        recv = int(getattr(self, "_call_audio_recv", 0) or 0)
        last_in = getattr(self, "_call_last_audio_in_at", None)
        if recv < 20 or not last_in:
            return False
        silent_for = time.monotonic() - float(last_in)
        if silent_for < 1.5:
            return False
        if self._peer_has_healthy_call_link(peer, transport):
            return False
        print(
            f"[call] Remote silent {silent_for:.1f}s with no healthy link — ending call"
        )
        self.call_end(peer_hash=peer, transport=transport)
        return True

    def call_send_audio(self, audio_b64, codec=OPUS_CODEC, call_id=None):
        if self.voice_call.state != STATE_ACTIVE:
            return False
        if not audio_b64:
            return False
        peer = self.voice_call.peer_hash
        cid = call_id or self.voice_call.call_id
        transport = self.voice_call.transport
        if self._maybe_end_call_remote_gone(peer, transport):
            return False
        link = self._call_link_for_peer(peer, transport, prefer_healthy=True)
        if not link:
            link = self._call_link_for_peer(peer, transport, prefer_healthy=False)
        if not link:
            now = time.monotonic()
            fails = int(getattr(self, "_call_send_link_fails", 0) or 0) + 1
            self._call_send_link_fails = fails
            fail_since = getattr(self, "_call_link_fail_since", None)
            if fail_since is None:
                self._call_link_fail_since = now
                print(
                    f"[call] No link to {peer[:16] if peer else '?'}... "
                    f"(waiting for reconnect)"
                )
            else:
                grace = 1.0 if not self._peer_has_healthy_call_link(peer, transport) else 4.0
                if (
                    (now - float(fail_since)) >= grace
                    and not getattr(self, "_call_ending", False)
                ):
                    print(
                        f"[call] No link to {peer[:16] if peer else '?'}... "
                        f"— ending call after reconnect timeout"
                    )
                    self.call_end()
            return False
        if not self._link_interface_healthy(link):
            now = time.monotonic()
            silent_since = getattr(self, "_call_remote_silent_since", None)
            last_in = getattr(self, "_call_last_audio_in_at", None)
            recv = int(getattr(self, "_call_audio_recv", 0) or 0)
            if recv >= 20 and last_in and (now - float(last_in)) >= 1.5:
                if silent_since is None:
                    self._call_remote_silent_since = now
                elif (
                    (now - float(silent_since)) >= 1.0
                    and not getattr(self, "_call_ending", False)
                ):
                    print(
                        f"[call] Peer silent on degraded link — ending call"
                    )
                    self.call_end()
                    return False
        else:
            self._call_remote_silent_since = None
        self._call_send_link_fails = 0
        self._call_link_fail_since = None
        link_mtu = int(getattr(link, "mtu", 1064) or 1064)
        chunks = split_call_audio_b64(
            audio_b64,
            codec,
            call_id=cid,
            link_mtu=link_mtu,
        )
        if not chunks:
            return False
        ok_any = False
        sent = int(getattr(self, "_call_audio_sent", 0) or 0)
        for chunk_b64 in chunks:
            seq = self.voice_call.next_audio_seq()
            ok = self._send_call_packet(peer, CALL_AUDIO, {
                "call_id": cid,
                "seq": seq,
                "codec": codec,
                "data": chunk_b64,
            }, self.voice_call.transport)
            if ok:
                ok_any = True
                sent += 1
                self._call_audio_sent = sent
                if sent <= 2 or sent % 40 == 0:
                    print(f"[call] Audio out #{sent} ({len(chunk_b64)} b64) → {peer[:16]}...")
        return ok_any

    def call_status(self):
        vc = self.voice_call
        peer = vc.peer_hash
        link = self._call_link_for_peer(peer, vc.transport) if peer else None
        rtt_ms = None
        link_mtu = None
        if link:
            link_mtu = int(getattr(link, "mtu", 0) or 0) or None
            rtt = getattr(link, "rtt", None)
            if rtt is not None:
                try:
                    rtt_ms = round(float(rtt) * 1000, 1)
                except (TypeError, ValueError):
                    rtt_ms = None
        return {
            "state": vc.state,
            "call_id": vc.call_id,
            "peer": peer,
            "transport": vc.transport,
            "rtt_ms": rtt_ms,
            "link_mtu": link_mtu,
            "audio_in": int(getattr(self, "_call_audio_recv", 0) or 0),
            "audio_out": int(getattr(self, "_call_audio_sent", 0) or 0),
        }
