# 使用Python 3.11作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
# - libmagic: 用于python-magic包的文件类型检测
# - libpng-dev: 用于PNG图像处理
# - libjpeg-dev: 用于JPEG图像处理
# - libwebp-dev: 用于WebP图像处理
# - libfreetype6-dev: 用于字体处理
# - libfontconfig1-dev: 字体配置
# - build-essential: 编译工具
RUN apt-get update && apt-get install -y \
    libmagic1 \
    libpng-dev \
    libjpeg-dev \
    libwebp-dev \
    libfreetype6-dev \
    libfontconfig1-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制项目文件
COPY pyproject.toml uv.lock ./
COPY optifuse/ ./optifuse/
COPY README.md LICENSE ./

# 安装uv包管理器
RUN pip install uv

# 使用uv安装Python依赖
RUN uv sync --frozen

# 创建符号链接使optifuse命令可用
RUN ln -sf /app/.venv/bin/optifuse /usr/local/bin/optifuse

# 设置环境变量
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# 创建输出目录
RUN mkdir -p /output

# 设置卷挂载点
VOLUME ["/output"]

# 设置工作目录为输出目录
WORKDIR /output

# 设置入口点
ENTRYPOINT ["optifuse"]

# 默认命令显示帮助信息
CMD ["--help"]
