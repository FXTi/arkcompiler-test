OPENHARMONY ?= OpenHarmony-7.0-Release
IMAGE ?= arkcompiler-test
.PHONY: prepare build verify test
prepare:
	python3 scripts/prepare.py --openharmony "$(OPENHARMONY)"
build: prepare
	docker build --platform linux/amd64 -t "$(IMAGE)" .
verify:
	docker run --rm --network none "$(IMAGE)" verify --execute
test:
	python3 -m unittest discover -s tests -v
	python3 scripts/smoke.py --image "$(IMAGE)"
