FROM ubuntu:latest

# install system-wide dependencies,
# python3 and the build-time dependencies for c modules
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 \
        python3-dev \
        python3-pip \
        python3-setuptools \
        python3-wheel \
        build-essential \
        ffmpeg \
        file

WORKDIR /src
COPY requirements.txt .

RUN pip3 install -r requirements.txt
RUN pip3 install uwsgi

# ffmpeg
