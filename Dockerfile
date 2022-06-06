ARG PYTHON_VERSION=3.9
ARG DEBIAN_VERSION=bullseye
ARG DOCKER_REGISTRY=docker.io

FROM ${DOCKER_REGISTRY}/python:${PYTHON_VERSION}-${DEBIAN_VERSION} as venv-stage
SHELL ["/bin/bash", "-c"]

# prepare
ARG PIP_INDEX_URL=https://pypi.org/simple/
RUN set -ex \
 && pip install --no-cache-dir --index-url ${PIP_INDEX_URL} --only-binary :all: twine setpypi \
 && python -m venv --upgrade-deps /opt/venv

ARG USABLE_HTTP_PROXY=""

COPY ./requirements*.txt /opt/

# download dependencies, wheel not wheeled once, upload them if PYPI_REPOSITORY_URL is set
ARG PYPI_REPOSITORY_URL=""
ARG EXTRA_PIP_INSTALL="ipython py-spy"
RUN set -ex \
 && mkdir -p /opt/wheels/ \
 && pip download --no-cache-dir --index-url ${PIP_INDEX_URL} --dest /opt/downloads/ ${EXTRA_PIP_INSTALL} -r /opt/requirements.txt \
 && find /opt/download/ -name '*.tar.gz' | xargs -r pip wheel --no-cache-dir --no-index --no-deps --wheel-dir /opt/wheels/

RUN set -ex \
 && [[ -z "${PYPI_REPOSITORY_URL}" ]] || { \
      setpypi -r ${PYPI_REPOSITORY_URL}; \
      find /opt/wheels/ -name '*.whl' | xargs -r twine upload --skip-existing ; \
      rm -rf ~/.pypirc ~/.config/pip/pip.conf; \
    }

# install requirements.txt into venv from /opt/wheels
RUN set -ex \
 && /opt/venv/bin/pip install --no-cache-dir --no-index --find-links /opt/wheels/ --find-links /opt/downloads/ ${EXTRA_PIP_INSTALL} -r /opt/requirements.txt \
 && du -hd2 /opt


FROM ${DOCKER_REGISTRY}/python:${PYTHON_VERSION}-slim-${DEBIAN_VERSION}
SHELL ["/bin/bash", "-c"]

ENV PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PATH=/opt/venv/bin:${PATH} \
    PYTHONPATH=/opt/venv/src/:${PYTHONPATH}

RUN set -ex \
 && apt update -y \
 && apt install -y --no-install-recommends --no-install-suggests \
          gosu \
          curl \
          htop \
          iputils-ping \
          ldnsutils \
          nano \
          net-tools \
          netcat \
          procps \
          traceroute \
          vim-tiny \
 && curl -Lo /tmp/pkg.deb https://github.com/Yelp/dumb-init/releases/download/v1.2.5/dumb-init_1.2.5_amd64.deb \
 && dpkg -i /tmp/pkg.deb \
 && rm -rf /var/lib/apt/lists/* /tmp/pkg.deb

RUN set -ex \
 && addgroup --gid 1000 avalon \
 && adduser --gid 1000 --uid 1000 --home /opt/venv --no-create-home --gecos "" --disabled-password avalon

# copy venv to current stage, must be same path
COPY --from=venv-stage /opt/venv /opt/venv

# here we instruct how this image will work
CMD ["dumb-init", "-cv", "gosu", "avalon", "/opt/venv/bin/python", "run.py"]
WORKDIR /opt/src/

COPY . /opt/src/
