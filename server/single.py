import random
import ssl
import websockets
import asyncio
import sys
import json
import argparse

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst 
gi.require_version('GstWebRTC', '1.0')
from gi.repository import GstWebRTC 
gi.require_version('GstSdp', '1.0')
from gi.repository import GstSdp 

# WEBRTCBIN = 'webrtcbin name=sendrecv latency=0'
# VSRC = 'uridecodebin uri=file:///home/projects/videos/lythaito.mp4 ! videoconvert ! videoscale ! video/x-raw,width=480,height=270'
# PIPELINE_DESC = WEBRTCBIN + '''
#  {vsrc} ! videoconvert ! queue !
#   vp8enc deadline=1 keyframe-max-dist=2000 ! rtpvp8pay picture-id-mode=15-bit !
#   queue ! application/x-rtp,media=video,encoding-name=VP8 ! sendrecv.
# '''.format(vsrc=VSRC)

PIPELINE_DESC = '''
 uridecodebin uri=file:///home/mq4060/quangnv/videos/lythaito.mp4 ! videoconvert ! videoscale ! video/x-raw,width=480,height=270 ! videoconvert ! queue !
  vp8enc deadline=1 keyframe-max-dist=2000 ! rtpvp8pay picture-id-mode=15-bit !
  queue ! application/x-rtp,media=video,encoding-name=VP8 ! webrtcbin name=sendrecv latency=0
'''

def print_status(msg):
    print(f'--- {msg}')

def print_error(msg):
    print(f'!!! {msg}', file=sys.stderr)

class WebRTCClient:
    def __init__(self, loop, our_id, server, remote_is_offerer):
        """
        Initialize the WebRTC send/receive handler.

        Args:
            loop (asyncio.AbstractEventLoop): The event loop to use for asynchronous operations.
            our_id (str): An optional user-specified ID we can use to register.
            server (str): The server address to connect to.
            remote_is_offerer (bool): Whether the remote peer will send the offer.

        Attributes:
            conn (object): The connection object (initially None).
            pipe (object): The pipe object (initially None).
            webrtc (object): The WebRTC object (initially None).
            event_loop (asyncio.AbstractEventLoop): The event loop to use for asynchronous operations.
            server (str): The server address to connect to.
            our_id (str): An optional user-specified ID we can use to register.
            id_ (str): The actual ID used to register (initially None).
            peer_id (str): An optional peer ID to connect to (initially None).
            remote_is_offerer (bool): Whether the remote peer will send the offer.
        """
        self.conn = None
        self.pipe = None
        self.webrtc = None
        self.event_loop = loop
        self.server = server
        # An optional user-specified ID we can use to register
        self.our_id = our_id
        # The actual ID we used to register
        self.id_ = None
        # An optional peer ID we should connect to
        self.peer_id = None
        # Whether we will send the offer or the remote peer will
        self.remote_is_offerer = remote_is_offerer

    async def send(self, msg):
        assert self.conn
        print(f'>>> {msg}')
        await self.conn.send(msg)

    async def connect(self):
        chain_pem, key_pem = 'cert.pem', 'key.pem'
        ssl_context = ssl.SSLContext()
        ssl_context.load_cert_chain(chain_pem, keyfile=key_pem)
        self.conn = await websockets.connect(self.server, ssl=ssl_context)
        if self.our_id is None:
            self.id_ = str(random.randrange(10, 10000))
        else:
            self.id_ = self.our_id
        await self.send(f'HELLO {self.id_}')

    async def setup_call(self):
        assert self.peer_id
        await self.send(f'SESSION {self.peer_id}')

    def send_soon(self, msg):
        asyncio.run_coroutine_threadsafe(self.send(msg), self.event_loop)

    def on_bus_poll_cb(self, bus):
        def remove_bus_poll():
            self.event_loop.remove_reader(bus.get_pollfd().fd)
            self.event_loop.stop()
        while bus.peek():
            msg = bus.pop()
            if msg.type == Gst.MessageType.ERROR:
                err = msg.parse_error()
                print("ERROR:", err.gerror, err.debug)
                remove_bus_poll()
                break
            elif msg.type == Gst.MessageType.EOS:
                remove_bus_poll()
                break
            elif msg.type == Gst.MessageType.LATENCY:
                self.pipe.recalculate_latency()

    def send_sdp(self, offer):
        text = offer.sdp.as_text()
        if offer.type == GstWebRTC.WebRTCSDPType.OFFER:
            print_status('Sending offer:\n%s' % text)
            msg = json.dumps({'sdp': {'type': 'offer', 'sdp': text}})
        elif offer.type == GstWebRTC.WebRTCSDPType.ANSWER:
            print_status('Sending answer:\n%s' % text)
            msg = json.dumps({'sdp': {'type': 'answer', 'sdp': text}})
        else:
            raise AssertionError(offer.type)
        self.send_soon(msg)

    def on_offer_created(self, promise, _, __):
        assert promise.wait() == Gst.PromiseResult.REPLIED
        reply = promise.get_reply()
        offer = reply['offer']
        promise = Gst.Promise.new()
        print_status('Offer created, setting local description')
        self.webrtc.emit('set-local-description', offer, promise)
        promise.interrupt()  # we don't care about the result, discard it
        self.send_sdp(offer)

    def on_negotiation_needed(self, _, create_offer):
        if create_offer:
            print_status('Call was connected: creating offer')
            promise = Gst.Promise.new_with_change_func(self.on_offer_created, None, None)
            self.webrtc.emit('create-offer', None, promise)

    def send_ice_candidate_message(self, _, mlineindex, candidate):
        icemsg = json.dumps({'ice': {'candidate': candidate, 'sdpMLineIndex': mlineindex}})
        self.send_soon(icemsg)

    def on_incoming_decodebin_stream(self, _, pad):
        if not pad.has_current_caps():
            print_error(pad, 'has no caps, ignoring')
            return

        caps = pad.get_current_caps()
        assert (len(caps))
        s = caps[0]
        name = s.get_name()
        if name.startswith('video'):
            q = Gst.ElementFactory.make('queue')
            conv = Gst.ElementFactory.make('videoconvert')
            sink = Gst.ElementFactory.make('autovideosink')
            self.pipe.add(q, conv, sink)
            self.pipe.sync_children_states()
            pad.link(q.get_static_pad('sink'))
            q.link(conv)
            conv.link(sink)

    def on_ice_gathering_state_notify(self, pspec, _):
        state = self.webrtc.get_property('ice-gathering-state')
        print_status(f'ICE gathering state changed to {state}')

    def on_incoming_stream(self, _, pad):
        if pad.direction != Gst.PadDirection.SRC:
            return

        decodebin = Gst.ElementFactory.make('decodebin')
        decodebin.connect('pad-added', self.on_incoming_decodebin_stream)
        self.pipe.add(decodebin)
        decodebin.sync_state_with_parent()
        pad.link(decodebin.get_static_pad('sink'))

    def start_pipeline(self, create_offer=True):
        print_status(f'Creating pipeline, create_offer: {create_offer}')
        self.pipe = Gst.parse_launch(PIPELINE_DESC)
        bus = self.pipe.get_bus()
        self.event_loop.add_reader(bus.get_pollfd().fd, self.on_bus_poll_cb, bus)
        self.webrtc = self.pipe.get_by_name('sendrecv')
        self.webrtc.connect('on-negotiation-needed', self.on_negotiation_needed, create_offer)
        self.webrtc.connect('on-ice-candidate', self.send_ice_candidate_message)
        self.webrtc.connect('notify::ice-gathering-state', self.on_ice_gathering_state_notify)
        self.webrtc.connect('pad-added', self.on_incoming_stream)
        self.pipe.set_state(Gst.State.PLAYING)

    def on_answer_created(self, promise, _, __):
        assert promise.wait() == Gst.PromiseResult.REPLIED
        reply = promise.get_reply()
        answer = reply['answer']
        promise = Gst.Promise.new()
        self.webrtc.emit('set-local-description', answer, promise)
        promise.interrupt()  # we don't care about the result, discard it
        self.send_sdp(answer)

    def on_offer_set(self, promise, _, __):
        assert promise.wait() == Gst.PromiseResult.REPLIED
        promise = Gst.Promise.new_with_change_func(self.on_answer_created, None, None)
        self.webrtc.emit('create-answer', None, promise)

    def handle_json(self, message):
        try:
            msg = json.loads(message)
        except json.decoder.JSONDecoderError:
            print_error('Failed to parse JSON message, this might be a bug')
            raise
        if 'sdp' in msg:
            sdp = msg['sdp']['sdp']
            if msg['sdp']['type'] == 'answer':
                print_status('Received answer:\n%s' % sdp)
                res, sdpmsg = GstSdp.SDPMessage.new_from_text(sdp)
                answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)
                promise = Gst.Promise.new()
                self.webrtc.emit('set-remote-description', answer, promise)
                promise.interrupt()  # we don't care about the result, discard it
            else:
                print_status('Received offer:\n%s' % sdp)
                res, sdpmsg = GstSdp.SDPMessage.new_from_text(sdp)

                if not self.webrtc:
                    self.start_pipeline(create_offer=False)

                assert self.webrtc

                offer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.OFFER, sdpmsg)
                promise = Gst.Promise.new_with_change_func(self.on_offer_set, None, None)
                self.webrtc.emit('set-remote-description', offer, promise)
        elif 'ice' in msg:
            assert self.webrtc
            ice = msg['ice']
            candidate = ice['candidate']
            sdpmlineindex = ice['sdpMLineIndex']
            self.webrtc.emit('add-ice-candidate', sdpmlineindex, candidate)
        else:
            print_error('Unknown JSON message')

    def close_pipeline(self):
        if self.pipe:
            self.pipe.set_state(Gst.State.NULL)
            self.pipe = None
        self.webrtc = None

    async def loop(self):
        assert self.conn
        async for message in self.conn:
            print(f'<<< {message}')
            if message == 'HELLO':
                if not self.peer_id:
                    print_status(f'Waiting for incoming call: ID is {self.id_}')
                else:
                    if self.remote_is_offerer:
                        print_status('Have peer ID: initiating call (will request remote peer to create offer)')
                    else:
                        print_status('Have peer ID: initiating call (will create offer)')
                    await self.setup_call()
            elif message == 'OFFER_REQUEST':
                print_status('Incoming call: we have been asked to create the offer')
                self.start_pipeline()
            elif message.startswith('ERROR'):
                print_error(message)
                self.close_pipeline()
                return 1
            else:
                self.handle_json(message)
                # exit()
        self.close_pipeline()
        return 0

    async def stop(self):
        if self.conn:
            await self.conn.close()
        self.conn = None



if __name__ == '__main__':
    Gst.init(None)
    parser = argparse.ArgumentParser()
    parser.add_argument('--our-id', help='String ID that the peer can use to connect to us')
    parser.add_argument('--server', default='wss://192.168.6.178:8443',
                        help='Signalling server to connect to, eg "wss://127.0.0.1:8443"')
    parser.add_argument('--remote-offerer', default=False, action='store_true',
                        dest='remote_is_offerer',
                        help='Request that the peer generate the offer and we\'ll answer')
    args = parser.parse_args()
    loop = asyncio.new_event_loop()
    c = WebRTCClient(loop, args.our_id, args.server, args.remote_is_offerer)
    loop.run_until_complete(c.connect())
    res = loop.run_until_complete(c.loop())
    sys.exit(res)
