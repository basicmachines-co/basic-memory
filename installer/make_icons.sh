#!/bin/bash

# Convert SVG to PNG at various required sizes
rsvg-convert -h 16 -w 16 icon.svg > icon_16x16.png
rsvg-convert -h 32 -w 32 icon.svg > icon_32x32.png
rsvg-convert -h 128 -w 128 icon.svg > icon_128x128.png
rsvg-convert -h 256 -w 256 icon.svg > icon_256x256.png
rsvg-convert -h 512 -w 512 icon.svg > icon_512x512.png

# Create iconset directory
mkdir -p Basic.iconset

# Move files into iconset with Mac-specific names
cp icon_16x16.png Basic.iconset/icon_16x16.png
cp icon_32x32.png Basic.iconset/icon_16x16@2x.png
cp icon_32x32.png Basic.iconset/icon_32x32.png
cp icon_128x128.png Basic.iconset/icon_32x32@2x.png
cp icon_256x256.png Basic.iconset/icon_128x128.png
cp icon_512x512.png Basic.iconset/icon_256x256.png
cp icon_512x512.png Basic.iconset/icon_512x512.png

# Convert iconset to icns
iconutil -c icns Basic.iconset

# Clean up
rm -rf Basic.iconset
rm icon_*.png