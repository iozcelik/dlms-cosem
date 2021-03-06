import logging

import attr

from dlms_cosem.protocol.hdlc import frames
from dlms_cosem.protocol.hdlc.exceptions import LocalProtocolError


LOG = logging.getLogger(__name__)


class _SentinelBase(type):
    """
    Sentinel values

     - Inherit identity-based comparison and hashing from object
     - Have a nice repr
     - Have a *bonus property*: type(sentinel) is sentinel

     The bonus property is useful if you want to take the return value from
     next_event() and do some sort of dispatch based on type(event).

     Taken from h11.
     """
    def __repr__(self):
        return self.__name__


def make_sentinel(name):
    cls = _SentinelBase(name, (_SentinelBase,), {})
    cls.__class__ = cls
    return cls


# NOT_CONNECTED is when we have created a session but not actually set up HDLC
# connection with the server (meter). We used a SNMR frame to set up the connection
NOT_CONNECTED = make_sentinel("NOT_CONNECTED")

# IDLE State is when we are connected but we have not started a data exchange or we
# just finished a data exchange
IDLE = make_sentinel("IDLE")

AWAITING_RESPONSE = make_sentinel("AWAITING_RESPONSE")

AWAITING_CONNECTION = make_sentinel("AWAITING_CONNECTION")

SHOULD_SEND_READY_TO_RECEIVE = make_sentinel("SHOULD_SEND_READY_TO_RECEIVE")

AWAITING_DISCONNECT = make_sentinel("AWAITING_DISCONNECT")

CLOSED = make_sentinel("CLOSED")

NEED_DATA = make_sentinel("NEED_DATA")

# TODO: segmentation handling is not working with this state layout.

HDLC_STATE_TRANSITIONS = {
    NOT_CONNECTED: {frames.SetNormalResponseModeFrame: AWAITING_CONNECTION},
    AWAITING_CONNECTION: {frames.UnNumberedAcknowledgmentFrame: IDLE},
    IDLE: {
        frames.InformationFrame: AWAITING_RESPONSE,
        frames.SegmentedInformationRequestFrame: AWAITING_RESPONSE,
        frames.DisconnectFrame: AWAITING_DISCONNECT,
    },
    AWAITING_RESPONSE: {
        frames.InformationFrame: IDLE,
        frames.SegmentedInformationResponseFrame: SHOULD_SEND_READY_TO_RECEIVE,
    },
    SHOULD_SEND_READY_TO_RECEIVE: {frames.ReceiveReadyFrame: AWAITING_RESPONSE},
    AWAITING_DISCONNECT: {frames.UnNumberedAcknowledgmentFrame: NOT_CONNECTED},
}


SEND_STATES = [NOT_CONNECTED, IDLE, SHOULD_SEND_READY_TO_RECEIVE]
RECEIVE_STATES = [AWAITING_CONNECTION, AWAITING_RESPONSE, AWAITING_DISCONNECT]

# TODO: does the ssn and rsn belong in the state? Comparing to H11 that is only
#   using types in the state not full objects. Maybe it should be stored on the
#   connection?


@attr.s(auto_attribs=True)
class HdlcConnectionState:
    """
    Handles state changes in HDLC, we only focus on Client implementation as of now.

    A HDLC frame is passed to `process_frame` and it moves the state machine to the
    correct state. If a frame is processed that is not set to be able to transition
    the state in the current state a LocalProtocolError is raised.
    """

    current_state: _SentinelBase = attr.ib(default=NOT_CONNECTED)
    client_ssn: int = attr.ib(default=0)
    client_rsn: int = attr.ib(default=0)
    server_ssn: int = attr.ib(default=0)
    server_rsn: int = attr.ib(default=0)

    def process_frame(self, frame):

        frame_type = type(frame)

        if frame_type == frames.InformationFrame:
            self._process_information_frame(frame)

        self._transition_state(type(frame))

    def _process_information_frame(self, frame: frames.InformationFrame):
        """
        When sending an information request the client ssn and client rsn should
        correspond to the current state. We also know that the server rrs should be one
        higher than the current
        """
        if frame.response_frame:

            if not frame.send_sequence_number == self.client_ssn:
                raise LocalProtocolError(
                    f"Send Sequence Number {frame.send_sequence_number} does not correspond"
                    f" with the current state of the HDLC connection {self.client_ssn}"
                )

            if not frame.receive_sequence_number == self.client_rsn:
                raise LocalProtocolError(
                    f"Receive Sequence number {frame.receive_sequence_number} does not "
                    f"correspond with the current state of the HDLC "
                    f"connection {self.client_rsn}"
                )

            self._increment_server_rsn()
            self._increment_client_ssn()

        else:
            if not frame.send_sequence_number == self.server_ssn:
                raise LocalProtocolError(
                    f"Send Sequence Number {frame.send_sequence_number} does not correspond"
                    f" with the current state of the HDLC connection {self.server_ssn}"
                )

            if not frame.receive_sequence_number == self.server_rsn:
                raise LocalProtocolError(
                    f"Receive Sequence number {frame.receive_sequence_number} does not "
                    f"correspond with the current state of the HDLC "
                    f"connection {self.server_rsn}"
                )
            self._increment_server_ssn()
            self._increment_client_rsn()

    def _increment_server_rsn(self):
        self.server_rsn += 1
        if self.server_rsn > 7:
            self.server_rsn = 0

    def _increment_server_ssn(self):
        self.server_ssn += 1
        if self.server_ssn > 7:
            self.server_ssn = 0

    def _increment_client_rsn(self):
        self.client_rsn += 1
        if self.client_rsn > 7:
            self.client_rsn = 0

    def _increment_client_ssn(self):
        self.client_ssn += 1
        if self.client_ssn > 7:
            self.client_ssn = 0

    def _transition_state(self, frame_type):
        try:
            new_state = HDLC_STATE_TRANSITIONS[self.current_state][frame_type]
        except KeyError:
            raise LocalProtocolError(
                f"can't handle frame type {frame_type} when state={self.current_state}"
            )
        old_state = self.current_state
        self.current_state = new_state
        LOG.debug(f"HDLC state transitioned from {old_state} to {new_state}")
