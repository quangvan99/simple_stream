# ubuntu 22.04
apt-get update
apt-get install gstreamer-1.0 -y
apt-get install -y gstreamer1.0-libav gstreamer1.0-tools
# apt-get install --reinstall -y gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly  libswresample-dev libavutil-dev libavutil56 libavcodec-dev libavcodec58 libavformat-dev libavformat58 libavfilter7 libde265-dev libde265-0 libx265-199 libx264-163 libvpx7 libmpeg2encpp-2.1-0 libmpeg2-4 libmpg123-0
apt-get install --reinstall -y gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly  libswresample-dev libavutil-dev libavutil56 libavcodec-dev libavcodec58 libavformat-dev libavformat58 libavfilter7 libde265-dev libde265-0 libx265-179 libx264-155 libvpx6 libmpeg2encpp-2.1-0 libmpeg2-4 libmpg123-0
apt-get install pkg-config libcairo2-dev gcc python3-dev libgirepository1.0-dev -y
apt-get install python3-pip -y
apt-get install python3-gst-1.0 -y
apt-get install gir1.2-gst-plugins-bad-1.0 gstreamer1.0-nice -y
apt-get install libgtk-3-dev -y
# pip install -U bitsandbytes
pip3 install meson ninja
pip3 install PyGObject
pip3 install websockets

echo "Deleting GStreamer cache"
rm -rf ~/.cache/gstreamer-1.0/
