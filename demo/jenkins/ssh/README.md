# Jenkins SSH 密钥目录

这里放 Jenkins 拉取 Git 私有仓库所需的 SSH 私钥。

## 操作流程

### 方案 A：演示环境直接用个人密钥（最快）

如果只是本地跑通 demo，没有独立的 Jenkins 服务账号，可以直接复用你已有的个人 SSH 密钥：

```powershell
# 查看你已有的密钥文件名
dir C:\Users\Admin\.ssh\

# 复制到 Jenkins ssh 目录（以 id_ed25519 为例，id_rsa 同理）
copy C:\Users\Admin\.ssh\id_ed25519     D:\work_space\build_system_plan\demo\jenkins\ssh\
copy C:\Users\Admin\.ssh\id_ed25519.pub D:\work_space\build_system_plan\demo\jenkins\ssh\
```

> ⚠️ 生产环境不要这样做，应为 Jenkins 创建独立的 Deploy Key。

### 方案 B：在 Windows 宿主机（PowerShell）生成新密钥

> 不需要进 WSL，不需要进任何 Docker 容器。直接在项目目录的 PowerShell 终端中执行：

```powershell
# 进入项目根目录 demo\jenkins\ssh
cd d:\work_space\build_system_plan\demo\jenkins\ssh

# 生成 ed25519 密钥对（Windows 10/11 自带 ssh-keygen）
ssh-keygen -t ed25519 -C "jenkins@ci.intra" -f id_ed25519 -N """"
```

生成两个文件：
- `id_ed25519` — 私钥（不要提交到 git，已被 .gitignore 忽略）
- `id_ed25519.pub` — 公钥

### 2. 添加 known_hosts（防止首次 ssh 连接卡住）

```powershell
# 仍在 demo\jenkins\ssh 目录下
ssh-keyscan gitea.intra >> known_hosts    # 替换为你的 Git 域名
ssh-keyscan gitlab.com >> known_hosts     # 如果用 gitlab.com
```

### 3. 把公钥放到 GitLab / Gitea 上

| Git 平台 | 路径 |
|---|---|
| Gitea | 项目 → Settings → Deploy Keys → Add Deploy Key（勾选 Write access 如果需要 push） |
| GitLab | 项目 → Settings → Repository → Deploy Keys |
| GitHub | 项目 → Settings → Deploy keys → Add deploy key |

### 4. 启动容器

私钥通过 compose 挂载自动进入 Jenkins 容器：

```yaml
# compose.yaml 中已配置
jenkins:
  volumes:
    - ./jenkins/ssh:/var/jenkins_home/.ssh:ro
```

容器内的 Jenkins 在 `git clone git@gitea.intra:...` 时，会自动使用 `/var/jenkins_home/.ssh/id_ed25519` 向 GitLab 证明身份。

## 原理说明

```
宿主机(WSL)生成密钥对 ──┬── 公钥 → GitLab Deploy Key（验证敲门人身份）
                       └── 私钥 → 挂载进 Jenkins 容器（敲门凭证）
```

私钥必须存在于发起 git clone 的一方——也就是 Jenkins 容器内部。公钥放在 GitLab 上用于验证。**生成在宿主机，使用在容器内，中间通过 volume 挂载传递**——这是 Docker 环境下 SSH 认证的标准模式，没有额外的网络传输风险。

## 注意事项

- 私钥权限必须为 600，`ssh-keygen` 默认即可
- 此目录下除 `README.md` 和 `.gitignore` 外，所有文件都不会被 git 提交
- 如果不想用 SSH，可以改用 HTTPS + token 方式拉代码（在 `.env` 中配置 `GIT_CLONE_URL`），但安全性不如 SSH deploy key