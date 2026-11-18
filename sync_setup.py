"""多机统计自动同步自助配置工具（支持 GitHub CLI 一键建库/绑库、任意 Git 远程仓库、或本地网盘目录）。

用法示例：
  1. 使用当前登录的 GitHub CLI 账号一键开启多机同步（第 1 台电脑自动建库，第 2 台电脑自动克隆合并）：
     python -X utf8 sync_setup.py

  2. 指定自己的仓库名，或显式创建为公开数据仓（默认创建为私有数据仓以保护设备名与项目目录名）：
     python -X utf8 sync_setup.py --repo my-ai-usage-sync
     python -X utf8 sync_setup.py --repo my-ai-usage-sync --public

  3. 绑定任意 Git 远程仓库（GitHub / GitLab / Gitee / 自建 Gitea / SSH，无需 gh CLI）：
     python -X utf8 sync_setup.py --remote git@github.com:yourname/ai-usage-sync.git

  4. 绑定本地云盘同步文件夹（OneDrive / iCloud Drive / Dropbox / Syncthing / NAS，无需 Git）：
     python -X utf8 sync_setup.py --dir "D:\\OneDrive\\ai-usage-sync"

  5. 查看当前同步状态 / 关闭多机同步：
     python -X utf8 sync_setup.py --status
     python -X utf8 sync_setup.py --disable
"""
import argparse
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SYNC_CFG = os.path.join(HERE, 'sync_dir.txt')
DEFAULT_LOCAL_SYNC_REPO = os.path.join(os.path.expanduser('~'), '.ai-usage-sync')


def run_cmd(cmd, cwd=None, timeout=90):
    try:
        r = subprocess.run(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
        return r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip()
    except FileNotFoundError:
        return 127, '', f'未找到命令: {cmd[0]}'
    except Exception as e:
        return 1, '', str(e)


def ensure_git_identity(repo_dir, fallback_name='ai-usage-monitor', fallback_email='ai-usage-monitor@users.noreply.github.com'):
    rc_n, name, _ = run_cmd(['git', '-C', repo_dir, 'config', 'user.name'])
    if rc_n != 0 or not name:
        run_cmd(['git', '-C', repo_dir, 'config', 'user.name', fallback_name])
    rc_e, email, _ = run_cmd(['git', '-C', repo_dir, 'config', 'user.email'])
    if rc_e != 0 or not email:
        run_cmd(['git', '-C', repo_dir, 'config', 'user.email', fallback_email])


def show_status():
    env_dir = os.environ.get('USAGE_SYNC_DIR', '').strip()
    cfg_dir = ''
    if os.path.exists(SYNC_CFG):
        try:
            cfg_dir = open(SYNC_CFG, encoding='utf-8').read().strip()
        except Exception:
            pass
    active = env_dir or cfg_dir
    if not active:
        # Check legacy in-place mode
        rc, out, _ = run_cmd(['git', '-C', HERE, 'check-ignore', '-q', 'data/usage-check.json'])
        if os.path.isdir(os.path.join(HERE, '.git')) and rc == 1 and not out:
            _, rem, _ = run_cmd(['git', '-C', HERE, 'remote', 'get-url', 'origin'])
            print(f'当前模式: 仓库内直连同步（{HERE}）')
            if rem:
                print(f'远程仓库: {rem}')
            data_dir = os.path.join(HERE, 'data')
        else:
            print('当前状态: 未开启多机同步（仅统计本机数据）')
            print('开启命令: python -X utf8 sync_setup.py')
            return
    else:
        target = os.path.abspath(os.path.expanduser(os.path.expandvars(active)))
        is_git = os.path.isdir(os.path.join(target, '.git'))
        print(f'当前状态: 已开启多机自动同步（{"Git 仓库模式" if is_git else "本地/云盘目录模式"}）')
        print(f'同步目录: {target}')
        if is_git:
            _, rem, _ = run_cmd(['git', '-C', target, 'remote', 'get-url', 'origin'])
            if rem:
                print(f'远程仓库: {rem}')
        data_dir = os.path.join(target, 'data') if (is_git or os.path.isdir(os.path.join(target, 'data'))) else target

    hosts = []
    if os.path.isdir(data_dir):
        for f in sorted(glob.glob(os.path.join(data_dir, 'usage-*.json'))):
            try:
                h = json.load(open(f, encoding='utf-8')).get('host') or os.path.basename(f)[6:-5]
                hosts.append(h)
            except Exception:
                hosts.append(os.path.basename(f))
    print('已汇聚设备: ' + (', '.join(hosts) if hosts else '（尚无数据文件，运行 agg.py 后自动生成）'))


def setup_plain_dir(folder):
    target = os.path.abspath(os.path.expanduser(os.path.expandvars(folder)))
    os.makedirs(target, exist_ok=True)
    with open(SYNC_CFG, 'w', encoding='utf-8') as fh:
        fh.write(target + '\n')
    print(f'==> 已绑定本地/云盘多机同步目录: {target}')


def setup_remote_git(remote_url, target_dir):
    target = os.path.abspath(os.path.expanduser(os.path.expandvars(target_dir)))
    if os.path.isdir(os.path.join(target, '.git')):
        print(f'==> 检测到已有同步目录 {target}，正在更新远程地址并拉取最新数据...')
        run_cmd(['git', '-C', target, 'remote', 'set-url', 'origin', remote_url])
        run_cmd(['git', '-C', target, 'pull', '--rebase', '--autostash'])
        ensure_git_identity(target)
    else:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        print(f'==> 正在克隆同步仓库 {remote_url} -> {target} ...')
        rc, out, err = run_cmd(['git', 'clone', remote_url, target], timeout=120)
        if rc != 0:
            # Might be an empty remote repository; initialize locally and bind origin
            os.makedirs(os.path.join(target, 'data'), exist_ok=True)
            run_cmd(['git', '-C', target, 'init', '-b', 'main'])
            ensure_git_identity(target)
            readme_p = os.path.join(target, 'README.md')
            if not os.path.exists(readme_p):
                with open(readme_p, 'w', encoding='utf-8') as fh:
                    fh.write('# AI 用量监控多机同步数据仓\n\n本仓库由 `ai-usage-monitor` 自动维护，仅存储 `data/usage-<主机名>.json` 纯统计数字，不含任何对话原文。\n')
            run_cmd(['git', '-C', target, 'add', 'README.md'])
            run_cmd(['git', '-C', target, 'commit', '-m', '初始化多机用量同步数据仓'])
            run_cmd(['git', '-C', target, 'remote', 'add', 'origin', remote_url])
            rc_p, _, err_p = run_cmd(['git', '-C', target, 'push', '-u', 'origin', 'main'], timeout=120)
            if rc_p != 0:
                print(f'❌ 无法推送到远程仓库 {remote_url}:\n{err or err_p}', file=sys.stderr)
                sys.exit(1)
        else:
            ensure_git_identity(target)
            os.makedirs(os.path.join(target, 'data'), exist_ok=True)

    with open(SYNC_CFG, 'w', encoding='utf-8') as fh:
        fh.write(target + '\n')
    print(f'==> 已绑定 Git 同步仓库: {remote_url}')


def setup_gh_auto(repo_arg, is_public, target_dir):
    rc, login, err = run_cmd(['gh', 'api', 'user', '--jq', '.login'])
    if rc != 0 or not login:
        print('❌ 未检测到已登录的 GitHub CLI (`gh`)。', file=sys.stderr)
        print('请任选以下一种方式：', file=sys.stderr)
        print('  1) 先执行 `gh auth login` 登录你自己的 GitHub 账号，再重新运行 `python -X utf8 sync_setup.py`；', file=sys.stderr)
        print('  2) 使用任意 Git 仓库：`python -X utf8 sync_setup.py --remote <你的Git仓库URL>`；', file=sys.stderr)
        print('  3) 使用网盘目录（OneDrive/iCloud/Dropbox）：`python -X utf8 sync_setup.py --dir <同步文件夹路径>`。', file=sys.stderr)
        sys.exit(1)

    run_cmd(['gh', 'auth', 'setup-git'])
    full_repo = repo_arg if '/' in repo_arg else f'{login}/{repo_arg}'
    target = os.path.abspath(os.path.expanduser(os.path.expandvars(target_dir)))
    fallback_email = f'{login}@users.noreply.github.com'

    print(f'==> 已识别当前 GitHub 账号: {login}，目标同步数据仓: {full_repo}')
    rc_view, _, _ = run_cmd(['gh', 'repo', 'view', full_repo, '--json', 'name'])

    if rc_view == 0:
        print(f'==> 检测到你的账号下已存在 {full_repo}，正在连接并拉取已有电脑的数据...')
        if os.path.isdir(os.path.join(target, '.git')):
            run_cmd(['git', '-C', target, 'pull', '--rebase', '--autostash'])
        else:
            if os.path.exists(target) and os.listdir(target):
                print(f'❌ 目标目录 {target} 已存在且非 Git 仓库，请通过 --target-dir 指定新路径。', file=sys.stderr)
                sys.exit(1)
            rc_c, _, err_c = run_cmd(['gh', 'repo', 'clone', full_repo, target], timeout=120)
            if rc_c != 0:
                print(f'❌ 克隆 {full_repo} 失败: {err_c}', file=sys.stderr)
                sys.exit(1)
        ensure_git_identity(target, login, fallback_email)
        os.makedirs(os.path.join(target, 'data'), exist_ok=True)
    else:
        vis_flag = '--public' if is_public else '--private'
        vis_cn = 'Public（公开）' if is_public else 'Private（私有，保护设备名与项目目录名）'
        print(f'==> 正在你的 GitHub 账号下自动创建同步数据仓 {full_repo} [{vis_cn}] ...')
        os.makedirs(os.path.join(target, 'data'), exist_ok=True)
        if not os.path.isdir(os.path.join(target, '.git')):
            run_cmd(['git', '-C', target, 'init', '-b', 'main'])
        ensure_git_identity(target, login, fallback_email)
        readme_p = os.path.join(target, 'README.md')
        if not os.path.exists(readme_p):
            with open(readme_p, 'w', encoding='utf-8') as fh:
                fh.write(
                    '# AI 用量监控多机自动同步数据仓\n\n'
                    '本仓库由 [ai-usage-monitor](https://github.com/ypyik0669/ai-usage-monitor) 的 `sync_setup.py` 自动创建与维护。\n'
                    '`data/usage-<主机名>.json` 仅包含聚合统计数字，不含任何对话文本。\n'
                )
        run_cmd(['git', '-C', target, 'add', 'README.md'])
        run_cmd(['git', '-C', target, 'commit', '-m', '初始化多机用量同步数据仓'])
        rc_cr, out_cr, err_cr = run_cmd(
            [
                'gh', 'repo', 'create', full_repo,
                vis_flag,
                '--source', target,
                '--remote', 'origin',
                '--description', 'AI 用量监控面板多机自动同步数据仓',
                '--push',
            ],
            timeout=120,
        )
        if rc_cr != 0:
            print(f'❌ 创建仓库失败: {err_cr or out_cr}', file=sys.stderr)
            sys.exit(1)

    with open(SYNC_CFG, 'w', encoding='utf-8') as fh:
        fh.write(target + '\n')
    print(f'==> 已写入同步配置 {SYNC_CFG} -> {target}')
    return full_repo


def main():
    ap = argparse.ArgumentParser(description='一键配置多台电脑用量自动同步（支持用户自己的 GitHub 账号 / 任意 Git 仓库 / 云盘目录）')
    ap.add_argument('--repo', default='ai-usage-sync', help='使用 GitHub CLI 自动创建或连接的仓库名（默认: ai-usage-sync）')
    ap.add_argument('--public', action='store_true', help='将新建的 GitHub 同步数据仓设为 Public（默认设为 Private 以保护主机名和项目名）')
    ap.add_argument('--remote', default='', help='指定任意 Git 远程仓库 URL（GitHub / GitLab / Gitee / SSH 等，无需 gh CLI）')
    ap.add_argument('--dir', default='', help='指定本地云盘同步目录（如 OneDrive / iCloud / Dropbox / NAS 路径，无需 Git）')
    ap.add_argument('--target-dir', default=DEFAULT_LOCAL_SYNC_REPO, help=f'Git 同步仓在本地的存放路径（默认: {DEFAULT_LOCAL_SYNC_REPO}）')
    ap.add_argument('--status', action='store_true', help='查看当前多机同步状态与已汇聚的设备列表')
    ap.add_argument('--disable', action='store_true', help='关闭外部多机同步（删除 sync_dir.txt，恢复仅本机统计）')
    args = ap.parse_args()

    if args.status:
        show_status()
        return

    if args.disable:
        if os.path.exists(SYNC_CFG):
            os.remove(SYNC_CFG)
            print('✅ 已关闭外部多机同步（已移除 sync_dir.txt）。')
        else:
            print('当前未启用 sync_dir.txt 外部同步。')
        return

    bound_repo = ''
    if args.dir:
        setup_plain_dir(args.dir)
    elif args.remote:
        setup_remote_git(args.remote, args.target_dir)
    else:
        bound_repo = setup_gh_auto(args.repo, args.public, args.target_dir)

    print('==> 正在执行首次本机用量上传与多机数据合并...')
    py = sys.executable.replace('pythonw.exe', 'python.exe')
    rc, out, err = run_cmd([py, '-X', 'utf8', os.path.join(HERE, 'agg.py')], cwd=HERE, timeout=180)
    if out:
        print(out)
    if rc != 0 and err:
        print(err, file=sys.stderr)

    print('')
    show_status()
    print('')
    print('✅ 多机自动同步配置完成！后台 serve.py 每 15 分钟会自动拉取并推送最新统计。')
    if bound_repo:
        repo_flag = '' if args.repo == 'ai-usage-sync' else f' --repo {args.repo}'
        print(f'💡 在你的另一台电脑上，只需登录同一个 GitHub 账号并运行同一条命令即可自动合并：')
        print(f'   python -X utf8 sync_setup.py{repo_flag}')


if __name__ == '__main__':
    main()
