# Docker 使用说明

## 构建镜像

```bash
# 构建Docker镜像
docker build -t optifuse .

# 或者使用docker-compose构建
docker-compose build
```

## 基本使用

### 1. 使用Docker命令

```bash
# 显示帮助信息
docker run --rm -v $(pwd):/output optifuse --help

# 优化单个文件
docker run --rm -v $(pwd):/output optifuse image.jpg

# 优化多个文件到指定目录
docker run --rm -v $(pwd):/output optifuse image1.jpg image2.png -d optimized/

# 从stdin读取并输出到stdout
cat image.jpg | docker run --rm -i -v $(pwd):/output optifuse --stdout
```

### 2. 使用docker-compose

```bash
# 显示帮助信息
docker-compose run --rm optifuse --help

# 优化文件
docker-compose run --rm optifuse image.jpg

# 优化多个文件到指定目录
docker-compose run --rm optifuse image1.jpg image2.png -d optimized/

# 使用开发环境（代码挂载）
docker-compose run --rm optifuse-dev --help
```

## 高级用法

### 批量处理

```bash
# 优化当前目录下的所有图片
docker run --rm -v $(pwd):/output optifuse *.jpg *.png *.svg

# 递归处理子目录
docker run --rm -v $(pwd):/output bash -c "find . -name '*.jpg' -o -name '*.png' -o -name '*.svg' | xargs optifuse"
```

### 自定义输出

```bash
# 输出到指定目录
docker run --rm -v $(pwd):/output optifuse image.jpg -d /output/optimized

# 输出到stdout
docker run --rm -v $(pwd):/output optifuse image.jpg --stdout

# 指定输出格式
docker run --rm -v $(pwd):/output optifuse image.jpg --stdout-artifact webp
```

### 图像优化选项

```bash
# 无损压缩
docker run --rm -v $(pwd):/output optifuse image.jpg --img-lossless

# 自定义质量
docker run --rm -v $(pwd):/output optifuse image.jpg --img-quality 90

# 限制最大尺寸
docker run --rm -v $(pwd):/output optifuse image.jpg --img-max 1920

# 添加alt文本
docker run --rm -v $(pwd):/output optifuse image.jpg --img-alt "描述文本"
```

### SVG优化选项

```bash
# 美化输出
docker run --rm -v $(pwd):/output optifuse image.svg --svg-pretty
```

### 字体优化选项

```bash
# 自定义字体族名
docker run --rm -v $(pwd):/output optifuse font.ttf --font-family "CustomFont"

# 自定义字重
docker run --rm -v $(pwd):/output optifuse font.ttf --font-weight bold

# 自定义字体样式
docker run --rm -v $(pwd):/output optifuse font.ttf --font-style italic

# 字体分割策略
docker run --rm -v $(pwd):/output optifuse font.ttf --font-split auto
```

## 环境变量

- `PYTHONUNBUFFERED=1`: 确保Python输出不被缓存
- `PYTHONPATH=/app`: 设置Python模块搜索路径

## 卷挂载

- `/output`: 输出目录，用于存储优化后的文件
- `/input`: 输入目录（只读），用于读取源文件

## 故障排除

### 权限问题

如果遇到权限问题，可以调整文件权限：

```bash
# 确保当前用户有写入权限
chmod 755 $(pwd)
```

### 内存不足

对于大文件处理，可能需要增加Docker内存限制：

```bash
docker run --rm -m 2g -v $(pwd):/output optifuse large_image.jpg
```

### 网络问题

如果在企业网络环境中，可能需要配置代理：

```bash
docker run --rm \
  -e HTTP_PROXY=http://proxy.company.com:8080 \
  -e HTTPS_PROXY=http://proxy.company.com:8080 \
  -v $(pwd):/output optifuse image.jpg
```

## 性能优化

### 多阶段构建

对于生产环境，可以考虑使用多阶段构建来减少镜像大小：

```dockerfile
# 构建阶段
FROM python:3.11-slim as builder
# ... 构建依赖

# 运行阶段
FROM python:3.11-slim
# ... 复制必要文件
```

### 缓存优化

使用 `.dockerignore` 文件排除不必要的文件，减少构建上下文大小。

### 并行处理

对于批量处理，可以使用并行执行：

```bash
# 使用GNU parallel并行处理
find . -name "*.jpg" | parallel -j 4 'docker run --rm -v $(pwd):/output optifuse {}'
```
