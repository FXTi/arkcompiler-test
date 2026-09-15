OPENHARMONY ?= OpenHarmony-7.0-Release
IMAGE := arkcompiler-test:latest
IMAGE_REF := ghcr.io/fxti/arkcompiler-test:latest
.PHONY: prepare build verify test tool-bundle push
prepare:
	python3 scripts/prepare.py --openharmony "$(OPENHARMONY)"
build: prepare
	docker build --platform linux/amd64 -t "$(IMAGE)" .
verify:
	docker run --rm --network none "$(IMAGE)" verify --execute
test:
	python3 -m unittest discover -s tests -v
	python3 scripts/smoke.py --image "$(IMAGE)"
tool-bundle:
	test -x .stage/bin/es2abc
	tar --zstd -C .stage -cf "$${TOOLS_BUNDLE:-arkcompiler-test-tools.tar.zst}" \
		bin lib sources licenses versions.json profiles.json cases.json build-info.json
push:
	docker image inspect "$(IMAGE)" >/dev/null
	docker tag "$(IMAGE)" "$(IMAGE_REF)"
	docker push "$(IMAGE_REF)"
