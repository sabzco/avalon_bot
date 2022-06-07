SHELL = /bin/bash
VERSION ?= $(shell scripts/git-version.sh)
REGISTRY ?= docker.io
DOCKER_REGISTRY ?= $(REGISTRY)/sabz-hobbies/avalon-bot

.PHONY: docker
docker:
		@command -v docker >/dev/null || echo 'Please install docker first'
		@if [ $${DOCKER_USER} ]; then if [ $${DOCKER_PASSWORD} ]; then docker login -u $${DOCKER_USER} -p $${DOCKER_PASS} $${REGISTRY} && export SUCCESS_LOGIN=true; else bash -c 'echo "Docker password not set"; exit 1'; fi; fi;
		@if [ $${PYTHON_VERSION} ]; then export BUILD_ARGS="--build-arg PYTHON_VERSION=$${PYTHON_VERSION}"; fi
		@if [ $${DEBIAN_VERSION} ]; then export BUILD_ARGS="$${BUILD_ARGS} --build-arg DEBIAN_VERSION=$${DEBIAN_VERSION}"; fi
		@if [ $${PIP_INDEX_URL} ]; then export BUILD_ARGS="$${BUILD_ARGS} --build-arg PIP_INDEX_URL=$${PIP_INDEX_URL}"; fi
		@if [ $${USABLE_HTTP_PROXY} ]; then export BUILD_ARGS="$${BUILD_ARGS} --build-arg USABLE_HTTP_PROXY=$${USABLE_HTTP_PROXY}"; fi
		@if [ $${PYPI_REPOSITORY_URL} ]; then export BUILD_ARGS="$${BUILD_ARGS} --build-arg PYPI_REPOSITORY_URL=$${PYPI_REPOSITORY_URL}"; fi
		@if [ $${EXTRA_PIP_INSTALL} ]; then export BUILD_ARGS="$${BUILD_ARGS} --build-arg EXTRA_PIP_INSTALL=$${EXTRA_PIP_INSTALL}"; fi
		@docker build -t "$(DOCKER_REGISTRY):$(VERSION)" $${BUILD_ARGS} .
		@if [ $${SUCCESS_LOGIN} ]; then docker push $(DOCKER_REGISTRY):$(VERSION); fi
