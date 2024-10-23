import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
gi.require_version('GstWebRTC', '1.0')
from gi.repository import GstWebRTC
gi.require_version('GstSdp', '1.0')
from gi.repository import GstSdp
import sys
import random
import ssl
import websockets
import asyncio
import json
import gbulb  # Import gbulb

def print_status(msg):
    print(f'--- {msg}')

def print_error(msg):
    print(f'!!! {msg}', file=sys.stderr)

class WebRTCClient:
    def __init__(self, our_id, peer_id, server, webrtc, event_loop):
        self.conn = None
        self.pipe = None
        self.webrtc = webrtc
        self.server = server
        self.our_id = our_id
        self.id_ = None
        self.peer_id = peer_id
        self.remote_is_offerer = False
        self.event_loop = event_loop

    async def send(self, msg):
        assert self.conn
        print(f'>>> {msg}')
        await self.conn.send(msg)

    async def connect(self):
        ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        self.conn = await websockets.connect(self.server, ssl=ssl_context)
        if self.our_id is None:
            self.id_ = str(random.randrange(10, 10000))
        else:
            self.id_ = self.our_id
        await self.send(f'HELLO {self.id_}')

        if self.peer_id and not self.remote_is_offerer:
            await self.setup_call()


    async def setup_call(self):
        assert self.peer_id
        await self.send(f'SESSION {self.peer_id}')

    def send_soon(self, msg):
        # Safely schedule the coroutine to run in the event loop from another thread
        asyncio.run_coroutine_threadsafe(self.send(msg), self.event_loop)

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

    def on_negotiation_needed(self, element):
        print_status('Negotiation needed')
        promise = Gst.Promise.new_with_change_func(self.on_offer_created, None, None)
        self.webrtc.emit('create-offer', None, promise)

    def send_ice_candidate_message(self, _, mlineindex, candidate):
        icemsg = json.dumps({'ice': {'candidate': candidate, 'sdpMLineIndex': mlineindex}})
        self.send_soon(icemsg)

    def on_ice_gathering_state_notify(self, element, pspec):
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

    def on_incoming_decodebin_stream(self, _, pad):
        if not pad.has_current_caps():
            print_error('Pad has no caps, ignoring')
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

    def connect_webrtc_signals(self):
        self.webrtc.connect('on-negotiation-needed', self.on_negotiation_needed)
        self.webrtc.connect('on-ice-candidate', self.send_ice_candidate_message)
        self.webrtc.connect('notify::ice-gathering-state', self.on_ice_gathering_state_notify)
        self.webrtc.connect('pad-added', self.on_incoming_stream)

    def on_answer_created(self, promise, _, __):
        assert promise.wait() == Gst.PromiseResult.REPLIED
        reply = promise.get_reply()
        answer = reply['answer']
        promise = Gst.Promise.new()
        self.webrtc.emit('set-local-description', answer, promise)
        promise.interrupt()
        self.send_sdp(answer)

    def on_offer_set(self, promise, _, __):
        assert promise.wait() == Gst.PromiseResult.REPLIED
        promise = Gst.Promise.new_with_change_func(self.on_answer_created, None, None)
        self.webrtc.emit('create-answer', None, promise)

    def handle_json(self, message):
        try:
            msg = json.loads(message)
        except json.decoder.JSONDecodeError:
            print_error(f'Failed to parse JSON message: {message}')
            return
        if 'sdp' in msg:
            sdp = msg['sdp']['sdp']
            if msg['sdp']['type'] == 'answer':
                print_status('Received answer:\n%s' % sdp)
                res, sdpmsg = GstSdp.SDPMessage.new_from_text(sdp)
                answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)
                promise = Gst.Promise.new()
                self.webrtc.emit('set-remote-description', answer, promise)
                promise.interrupt()
            else:
                print_status('Received offer:\n%s' % sdp)
                res, sdpmsg = GstSdp.SDPMessage.new_from_text(sdp)

                offer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.OFFER, sdpmsg)
                promise = Gst.Promise.new_with_change_func(self.on_offer_set, None, None)
                self.webrtc.emit('set-remote-description', offer, promise)
        elif 'ice' in msg:
            ice = msg['ice']
            candidate = ice['candidate']
            sdpmlineindex = ice['sdpMLineIndex']
            self.webrtc.emit('add-ice-candidate', sdpmlineindex, candidate)
        else:
            print_error('Unknown JSON message')

    def close_pipeline(self):
        pass  # Do not set the pipeline to NULL here since it's shared

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
                        await self.setup_call()
                    else:
                        print_status('Have peer ID: initiating call (will create offer)')
                        # Start the pipeline here if necessary
                        pass  # We already started the pipeline
            elif message == 'OFFER_REQUEST':
                print_status('Incoming call: we have been asked to create the offer')
                # Start negotiation
                pass  # No action needed; on_negotiation_needed will handle it
            elif message == 'SESSION_OK':
                print_status('Session established successfully')
                # You can add any session setup confirmation logic here if needed
            elif message.startswith('ERROR'):
                print_error(message)
                self.close_pipeline()
                return 1
            else:
                self.handle_json(message)
        self.close_pipeline()
        return 0

    async def stop(self):
        if self.conn:
            await self.conn.close()
        self.conn = None

async def main():

    PIPELINE_DESC = '''
    uridecodebin uri=file:///home/mq4060/quangnv/videos/lythaito.mp4 ! identity sync=true ! 
    videoconvert ! videoscale ! video/x-raw,width=480,height=270 ! videoconvert ! queue ! 
    vp8enc deadline=1 keyframe-max-dist=2000 ! tee name=t \

    '''
    # Initialize GStreamer and GLib Main Loop
    Gst.init(None)

    # Get the event loop
    loop = asyncio.get_event_loop()

    pairs = [
        [None, 1436],
        [None, 3970],
        [None, 2384],
        [None, 8962]
    ]

    for i in range(len(pairs)):
        PIPELINE_DESC += f""" t. ! queue leaky=downstream max-size-buffers=100 ! rtpvp8pay picture-id-mode=15-bit ! 
        queue ! application/x-rtp,media=video,encoding-name=VP8,payload={96+i} ! webrtcbin name=sendrecv{i} latency=0 """
    # Create the pipeline
    pipe = Gst.parse_launch(PIPELINE_DESC)

    clients = []
    for i, (our_id, peer_id) in enumerate(pairs):
        webrtc = pipe.get_by_name(f'sendrecv{i}')
        client = WebRTCClient(our_id, peer_id, 'wss://192.168.6.178:8443', webrtc, loop)
        client.pipe = pipe
        client.connect_webrtc_signals()
        clients.append(client)

    # Start the pipeline once
    pipe.set_state(Gst.State.PLAYING)

    tasks = []
    # Connect to the server
    for client in clients:
        await client.connect()
        tasks.append(asyncio.ensure_future(client.loop()))

    # Wait for both tasks to complete
    await asyncio.gather(*tasks)

    # Clean up
    pipe.set_state(Gst.State.NULL)

if __name__ == "__main__":
    # Install gbulb to integrate asyncio with GLib
    gbulb.install()

    # Get the default event loop
    loop = asyncio.get_event_loop()

    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
