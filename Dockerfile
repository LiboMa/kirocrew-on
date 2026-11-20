# KiroCrew ON 单机版的 Docker 渠道（MVP 0.2.0 P2 Task 8，设计 §4.4，S25）：一个容器、一个边界（personal），用于试用。
#   docker build -t kirocrew-on:<版本> --build-arg VERSION=<版本> .
#   docker run -d --name kirocrew-on -p 127.0.0.1:8918:8918 -v kirocrew-on:/var/lib/octopus -e AWS_REGION=us-east-1 \
#     --cap-add SYS_ADMIN --security-opt apparmor=unconfined kirocrew-on:<版本>
# 控制台只发布在本机回环上；首次启动在 docker logs 里打印一次管理员密码。Claude Code 与 Codex 经 Amazon Bedrock：给容器
# AWS 凭证（EC2 上把实例元数据的跳数设为 2）。KiroCrew 的沙箱要用 unshare / mount，所以要 SYS_ADMIN。
# 依赖的版本全部来自 deploy/versions.env（与发布清单相同），每个下载都校验；linux/amd64 与 linux/arm64 由 CI 构建。
FROM public.ecr.aws/amazonlinux/amazonlinux:2023
ARG VERSION
LABEL org.opencontainers.image.title="KiroCrew ON" org.opencontainers.image.version="${VERSION}"
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
COPY . /opt/kirocrew-on/releases/${VERSION}
RUN [ -n "${VERSION}" ] && [ "$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' /opt/kirocrew-on/releases/${VERSION}/octopus/__init__.py)" = "${VERSION}" ] \
    || { echo "VERSION 与 octopus/__init__.py 的版本不一致" >&2; exit 1; }; \
    ln -sfn /opt/kirocrew-on/releases/${VERSION} /opt/kirocrew-on/current; \
    bash /opt/kirocrew-on/current/deploy/docker/build.sh
VOLUME /var/lib/octopus
EXPOSE 8918
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/kirocrew-on/current/deploy/docker/entrypoint.sh"]
