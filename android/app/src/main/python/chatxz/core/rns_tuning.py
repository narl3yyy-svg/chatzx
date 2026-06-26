"""chatxz-wide RNS performance tuning (private chatxz network only)."""

import math


CHATXZ_RNS_MTU = 1064  # matches RNS UDPInterface.HW_MTU


def patch_tcp_server_ifac_netname():
    """RNS TCPServerInterface.incoming_connection expects ifac_netname on self."""
    try:
        from RNS.Interfaces.TCPInterface import TCPServerInterface
    except Exception:
        return
    if getattr(TCPServerInterface, "_chatxz_ifac_patch", False):
        return
    original = TCPServerInterface.incoming_connection

    def incoming_connection(self, handler=None):
        if not hasattr(self, "ifac_netname"):
            self.ifac_netname = None
        if not hasattr(self, "ifac_netkey"):
            self.ifac_netkey = None
        return original(self, handler=handler)

    TCPServerInterface.incoming_connection = incoming_connection
    TCPServerInterface._chatxz_ifac_patch = True


def apply_chatxz_rns_tuning():
    """Raise RNS MTU above the 500B default for much faster LAN file transfers."""
    patch_tcp_server_ifac_netname()
    import RNS

    mtu = CHATXZ_RNS_MTU
    RNS.Reticulum.MTU = mtu
    RNS.Reticulum.MDU = mtu - RNS.Reticulum.HEADER_MAXSIZE - RNS.Reticulum.IFAC_MIN_SIZE
    RNS.Packet.MDU = RNS.Reticulum.MDU
    RNS.Resource.SDU = RNS.Packet.MDU
    RNS.Link.MDU = (
        math.floor(
            (
                mtu
                - RNS.Reticulum.IFAC_MIN_SIZE
                - RNS.Reticulum.HEADER_MINSIZE
                - RNS.Identity.TOKEN_OVERHEAD
            )
            / RNS.Identity.AES128_BLOCKSIZE
        )
        * RNS.Identity.AES128_BLOCKSIZE
        - 1
    )