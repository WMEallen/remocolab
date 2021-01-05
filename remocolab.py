import apt, apt.debfile
import pathlib, stat, shutil, urllib.request, subprocess, getpass, time
import secrets, json, re, sys

import warnings
warnings.simplefilter('ignore', UserWarning)

import IPython.utils.io


region = 'eu'
token = None
install = []
username = 'colab'
blacklist = []
command = None


def _log(message):
        print('[%s] %s' % (time.strftime('%H:%M:%S', time.localtime()), message))


def _installPkg(cache, name):
    pkg = cache[name]
    if pkg.is_installed:
        pass
    else:
        pkg.mark_install()

def _installPkgs(cache, *args):
    for i in args:
        _installPkg(cache, i)

def _download(url, path):
    try:
        with urllib.request.urlopen(url) as response:
            with open(path, 'wb') as outfile:
                shutil.copyfileobj(response, outfile)
    except:
        print("Failed to download ", url)
        raise

def _get_gpu_name():
    r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], stdout = subprocess.PIPE, universal_newlines = True)
    if r.returncode != 0:
        return None
    return r.stdout.strip()

def _check_gpu_available():
    gpu_name = _get_gpu_name()
    if gpu_name == None:
        _log('No GPU detected.')
        _log('Attempting anyways...')
    else:
        _log('Detected GPU: %s' % gpu_name)

    if gpu_name in blacklist:
        sys.exit('GPU type "%s" is in the blacklist.' % gpu_name)


def _setupSSHDImpl(ngrok_token, ngrok_region):
    _check_gpu_available()

    _log('Updating packages...')
    cache = apt.Cache()
    cache.update()
    cache.open(None)
    cache.upgrade()
    cache.commit()

    _log('Unminimizing server...')
    subprocess.run(["unminimize"], input = "y\n", check = True, universal_newlines = True)

    _log('Downloading and installing ngrok...')
    _download("https://bin.equinox.io/c/4VmDzA7iaHb/ngrok-stable-linux-amd64.zip", "ngrok.zip")
    shutil.unpack_archive("ngrok.zip")
    pathlib.Path("ngrok").chmod(stat.S_IXUSR)

    subprocess.run(['adduser', '--disabled-password', '--gecos', '', username])

    with open('/etc/sudoers', 'a') as f:
        f.write('\n%s ALL=(ALL) NOPASSWD: ALL' % username)
        f.close()

    with open('/home/%s/.bashrc' % username, 'a') as f:
        f.write('cd /home/%s\n' % username)
        f.close()

    if not pathlib.Path('/root/.ngrok2/ngrok.yml').exists():
        subprocess.run(["./ngrok", "authtoken", ngrok_token])

    _log('Creating ngrok tunnel...')
    ngrok_proc = subprocess.Popen(["./ngrok", "http", "-region", ngrok_region, "6080"])
    time.sleep(2)
    if ngrok_proc.poll() != None:
        raise RuntimeError("Failed to run ngrok. Return code:" + str(ngrok_proc.returncode) + "\nSee runtime log for more info.")

    with urllib.request.urlopen("http://localhost:4040/api/tunnels") as response:
        url = json.load(response)['tunnels'][1]['public_url'].replace('http://', 'https://')

    _log('Setting up noVNC...')
    subprocess.run(['git', 'clone', 'https://github.com/novnc/noVNC.git'])
    subprocess.Popen(['noVNC/utils/launch.sh', '--vnc', 'localhost:5901'])

    return url


def _setup_nvidia_gl():
    # Install TESLA DRIVER FOR LINUX X64.
    # Kernel module in this driver is already loaded and cannot be neither removed nor updated.
    # (nvidia, nvidia_uvm, nvidia_drm. See dmesg)
    # Version number of nvidia driver for Xorg must match version number of these kernel module.
    # But existing nvidia driver for Xorg might not match.
    # So overwrite them with the nvidia driver that is same version to loaded kernel module.
    ret = subprocess.run(
                    ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                    stdout = subprocess.PIPE,
                    check = True,
                    universal_newlines = True)
    nvidia_version = ret.stdout.strip()
    nvidia_url = "https://us.download.nvidia.com/tesla/{0}/NVIDIA-Linux-x86_64-{0}.run".format(nvidia_version)
    _download(nvidia_url, "nvidia.run")
    pathlib.Path("nvidia.run").chmod(stat.S_IXUSR)
    subprocess.run(["./nvidia.run", "--no-kernel-module", "--ui=none"], input = "1\n", check = True, universal_newlines = True)

    #https://virtualgl.org/Documentation/HeadlessNV
    subprocess.run(["nvidia-xconfig",
                    "-a",
                    "--allow-empty-initial-configuration",
                    "--virtual=1920x1200",
                    "--busid", "PCI:0:4:0"],
                    check = True)

    with open("/etc/X11/xorg.conf", "r") as f:
        conf = f.read()
        conf = re.sub('(Section "Device".*?)(EndSection)',
                      '\\1    MatchSeat      "seat-1"\n\\2',
                      conf, 1, re.DOTALL)

    with open("/etc/X11/xorg.conf", "w") as f:
        f.write(conf)

    subprocess.run(["/opt/VirtualGL/bin/vglserver_config", "-config", "+s", "+f"], check = True)

    # Run Xorg server
    # VirtualGL and OpenGL application require Xorg running with nvidia driver to get Hardware 3D Acceleration.
    #
    # Without "-seat seat-1" option, Xorg try to open /dev/tty0 but it doesn't exists.
    # You can create /dev/tty0 with "mknod /dev/tty0 c 4 0" but you will get permision denied error.
    subprocess.Popen(["Xorg", "-seat", "seat-1", "-allowMouseOpenFail", "-novtswitch", "-nolisten", "tcp"])

def _setupVNC(url):
    libjpeg_ver = "2.0.3"
    virtualGL_ver = "2.6.2"
    turboVNC_ver = "2.2.3"

    libjpeg_url = "https://svwh.dl.sourceforge.net/project/libjpeg-turbo/{0}/libjpeg-turbo-official_{0}_amd64.deb".format(libjpeg_ver)
    virtualGL_url = "https://svwh.dl.sourceforge.net/project/virtualgl/{0}/virtualgl_{0}_amd64.deb".format(virtualGL_ver)
    turboVNC_url = "https://svwh.dl.sourceforge.net/project/turbovnc/{0}/turbovnc_{0}_amd64.deb".format(turboVNC_ver)

    _log('Installing VNC packages...')
    _download(libjpeg_url, "libjpeg-turbo.deb")
    _download(virtualGL_url, "virtualgl.deb")
    _download(turboVNC_url, "turbovnc.deb")

    cache = apt.Cache()
    apt.debfile.DebPackage("libjpeg-turbo.deb", cache).install()
    apt.debfile.DebPackage("virtualgl.deb", cache).install()
    apt.debfile.DebPackage("turbovnc.deb", cache).install()

    _log('Installing desktop environment...')
    _installPkgs(cache, "xfce4", "xfce4-terminal", "fonts-noto")
    cache.commit()

    if install:
        _log('Installing extra packages...')
        for pkg in install:
            _installPkgs(cache, pkg)
        cache.commit()

    vnc_sec_conf_p = pathlib.Path("/etc/turbovncserver-security.conf")
    vnc_sec_conf_p.write_text("""\
no-remote-connections
no-httpd
no-x11-tcp-connections
""")

    _log('Installing GPU driver...')
    gpu_name = _get_gpu_name()
    if gpu_name != None:
        _setup_nvidia_gl()

    _log('Starting VNC server...')
    vncrun_py = pathlib.Path("vncrun.py")
    vncrun_py.write_text("""\
import subprocess, secrets, pathlib, time

vnc_passwd = secrets.token_urlsafe()[:8]
vnc_viewonly_passwd = secrets.token_urlsafe()[:8]
print(vnc_passwd)
vncpasswd_input = "{0}\\n{1}".format(vnc_passwd, vnc_viewonly_passwd)
vnc_user_dir = pathlib.Path.home().joinpath(".vnc")
vnc_user_dir.mkdir(exist_ok=True)
vnc_user_passwd = vnc_user_dir.joinpath("passwd")
with vnc_user_passwd.open('wb') as f:
    subprocess.run(
        ["/opt/TurboVNC/bin/vncpasswd", "-f"],
        stdout=f,
        input=vncpasswd_input,
        universal_newlines=True)
vnc_user_passwd.chmod(0o600)
subprocess.run(
    ["/opt/TurboVNC/bin/vncserver"]
)

#Disable screensaver because no one would want it.
(pathlib.Path.home() / ".xscreensaver").write_text("mode: off\\n")

time.sleep(5)
subprocess.run(['gsettings', 'set', 'org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:b1dcc9dd-5262-4d8d-a863-c897e6d979b9/', 'use-system-font', 'false'], env={'DISPLAY': ':1'})
subprocess.run(['gsettings', 'set', 'org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:b1dcc9dd-5262-4d8d-a863-c897e6d979b9/', 'font', 'Noto Mono 10'], env={'DISPLAY': ':1'})
""")
    r = subprocess.run(
                    ["su", "-c", "python3 vncrun.py", username],
                    check = True,
                    stdout = subprocess.PIPE,
                    universal_newlines = True)
    if command:
        _log('Running command: %s' % command)
        subprocess.run(['su', '-c', 'cd $HOME; ' + command, username])
    _log('Ready! Click here to connect: %s/vnc.html?autoconnect=1&resize=remote&password=%s' % (url, r.stdout))

def setup():
    if not region or not re.match('^[a-z]{2}$', region, re.I):
        sys.exit('ERROR: Invalid region code. Ensure you have chosen a two-letter region code (eg. "eu", "us", "jp") and try again.')
    if not token or not re.match('^\w{49}$', token):
        sys.exit('ERROR: Invalid ngrok authtoken. Ensure you have copied the token from https://dashboard.ngrok.com/auth and try again.')

    # Set up the base system
    url = _setupSSHDImpl(token, region)

    # Set up VNC
    _setupVNC(url)

    # Prevent the runtime from timing out automatically
    while True:
        time.sleep(1)
