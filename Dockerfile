ARG BASE_IMAGE=ubuntu:22.04
FROM ${BASE_IMAGE} AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 libstdc++6 libgcc-s1 \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/arkcompiler-test/bin:${PATH}" \
    LD_LIBRARY_PATH=/opt/arkcompiler-test/lib \
    ARK_TEST_ROOT=/opt/arkcompiler-test \
    PYTHONDONTWRITEBYTECODE=1 LC_ALL=C.UTF-8 TZ=UTC
COPY .stage/ /opt/arkcompiler-test/
COPY app/arktest.py /opt/arkcompiler-test/arktest.py
RUN ln -s /opt/arkcompiler-test/arktest.py /opt/arkcompiler-test/bin/arkcompiler-test \
    && chmod 755 /opt/arkcompiler-test/arktest.py
FROM runtime AS generate
RUN arkcompiler-test generate && arkcompiler-test verify --execute
FROM runtime AS final
COPY --from=generate /opt/arkcompiler-test/corpus /opt/arkcompiler-test/corpus
WORKDIR /work
ENTRYPOINT ["arkcompiler-test"]
CMD ["info"]
