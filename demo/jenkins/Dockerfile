# demo Jenkins — 预装 JCasC + 必备插件
FROM jenkins/jenkins:lts-jdk17

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl jq git-lfs python3 python3-pip && \
    pip3 install --break-system-packages awscli && \
    rm -rf /var/lib/apt/lists/*
USER jenkins

# 预装插件（全入 plugins.txt → 启动时自动安装）
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

# JCasC 启动配置 — 放在 /usr/share/jenkins/ref，不会被 volume 覆盖
ENV CASC_JENKINS_CONFIG=/usr/share/jenkins/ref/casc
COPY casc/ /usr/share/jenkins/ref/casc/

# Groovy init scripts — fix credentials when encryption keys change
COPY init.groovy.d/ /usr/share/jenkins/ref/init.groovy.d/

# ---------- SSH 密钥：用于拉取 GitLab 私有仓库 ----------
# 构建时生成 or 使用挂载进来的密钥均可。
# 推荐方案：在宿主机生成 → 添加公钥到 GitLab Deploy Key → 挂载到容器
#   ssh-keygen -t ed25519 -C "jenkins@ci.intra" -f ./jenkins/ssh/id_ed25519 -N ""
#   cat jenkins/ssh/id_ed25519.pub → GitLab 项目 Settings → Repository → Deploy Keys
RUN mkdir -p /var/jenkins_home/.ssh \
             /var/jenkins_home/builds \
             /var/jenkins_home/jobs && \
    ssh-keyscan gitlab.com >> /var/jenkins_home/.ssh/known_hosts 2>/dev/null || true
# 如果你用自建 GitLab/Gitea，把对应的 host 也加进来：
# RUN ssh-keyscan gitea.intra >> /var/jenkins_home/.ssh/known_hosts
