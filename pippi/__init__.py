import dsp
import __main__
import multiprocessing as mp
import time
import os
import sys

# Try to enable midi support
try:
    import pygame.midi
except ImportError:
    print 'Midi support disabled. Please install pygame.'


# Doing a fallback dance for playback support
try:
    import alsaaudio
    audio_engine = 'alsa'
except ImportError:
    try:
        import pyaudio
        audio_engine = 'portaudio'
        print 'Using experimental portaudio bindings. Please install alsaaudio if you have problems with sound playback.'
    except ImportError:
        print 'Playback disabled. Please install alsaaudio (ALSA) or pyaudio (PortAudio)'



class EventManager():
    def __init__(self, ns, console):
        self.ns = ns
        self.console = console
        self.run = True

    ## todo: this would be better as an osc server
    def loop(self):
        while self.run == True:
            dsp.delay(4410)

            if hasattr(self.ns, 'console_cmds'):
                cmds = self.ns.console_cmds
                del self.ns.console_cmds

                for cmd in cmds:
                    cmd = cmd.split(' ')
                    cmd_function = cmd.pop(0)
                    args = ' '.join(cmd)
                    if hasattr(console, 'do_%s' % cmd_function):
                        method = getattr(console, 'do_%s' % cmd_function)
                        method(args)

    def midi_handler(self):
        pass

    def osc_handler(self):
        pass

    def cmd_handler(self):
        pass

class MidiManager():
    def __init__(self, device_id, ns):
        self.device_id = device_id
        self.ns = ns
        self.offset = None

    def setOffset(self, offset):
        self.offset = offset

    def geti(self, cc, default=None, low=0, high=1):
        return int(round(self.get(cc, default, low, high)))

    def getr(self, cc, default=None, low=0, high=1, spread=1):
        spread = 1 if spread > 1 else spread
        spread = dsp.rand(0, spread)

        value = self.get(cc, default, low, high)

        return value * spread

    def getri(self, cc, default=None, low=0, high=1, spread=1):
        return int(round(self.getr(cc, default, low, high, spread)))

    def get(self, cc, default=None, low=0, high=1):
        if self.offset is not None:
            cc = cc + self.offset

        try:
            value = getattr(self.ns, 'cc%s%s' % (self.device_id, cc))
            return (int(value) / 127.0) * (high - low) + low
        except Exception:
            if default is None:
                default = low

            return default

class ParamManager():
    def __init__(self, ns):
        self.ns = ns
        self.namespace = 'global'

    def setNamespace(self, namespace):
        self.namespace = namespace

    def set(self, param, value, namespace=None):
        if namespace is None:
            namespace = self.namespace

        params = self.getAll(namespace)

        params[param] = value

        setattr(self.ns, namespace, params)

    def get(self, param, default=None, namespace=None, throttle=None):
        if namespace is None:
            namespace = self.namespace

        if throttle is not None:
            last_updated = self.get('%s-last_updated' % name, time.time(), namespace='meta')

            if time.time() - last_updated >= interval:
                self.set('%s-last_updated' % name, time.time(), namespace='meta')
                self.set(name, default, namespace)

            return self.get(name, default, namespace)

        params = self.getAll(namespace)

        value = params.get(param, None)

        if value is None:
            value = default
            self.set(param, value, namespace)

        return value

    def getAll(self, namespace=None):
        if namespace is None:
            namespace = self.namespace

        try:
            params = getattr(self.ns, namespace)
        except AttributeError:
            params = {}

        return params

class IOManager():
    def __init__(self):
        self.manager = mp.Manager()
        self.ns = self.manager.Namespace()

        self.ns.device = 'default'

        self.ns.midi_devices = []
        self.ns.midi_listeners = []

        self.m = mp.Process(target=self.capture_midi, args=(self.ns,))
        self.m.start()

    def __del__(self):
        self.out.stop_stream()
        self.out.close()
        self.p.terminate()
        for listener in self.ns.midi_listeners:
            del listener

    def capture_midi(self, ns):
        try:
            pygame.midi.init()

            for device_id in self.ns.midi_devices:
                self.ns.midi_listeners[device_id] = pygame.midi.Input(device_id) # TODO add to config / init

            while True:
                for device_id in self.ns.midi_devices:
                    if device_id not in self.ns.midi_listeners:
                        self.ns.midi_listeners[device_id] = pygame.midi.Input(device_id)

                    if self.ns.midi_listeners[device_id].poll():
                        midi_events = self.ns.midi_listeners[device_id].read(10)

                        for e in midi_events:
                            # timestamp = e[1]
                            # cc = e[0][1]
                            # value = e[0][2]
                            setattr(ns, 'cc%s%s' % (device_id, e[0][1]), e[0][2])

                time.sleep(0.05)

        except pygame.midi.MidiException:
            dsp.log('Midi device not found')

    def open_alsa_pcm(self, device='default'):
        try:
            out = alsaaudio.PCM(alsaaudio.PCM_PLAYBACK, alsaaudio.PCM_NORMAL, device)
        except:
            print 'Could not open an ALSA connection.'
            return False

        out.setchannels(2)
        out.setrate(44100)
        out.setformat(alsaaudio.PCM_FORMAT_S16_LE)
        out.setperiodsize(10)

        return out


    def open_pyaudio_pcm(self, device='default'):
        self.p = pyaudio.PyAudio()

        out = self.p.open(
            format = self.p.get_format_from_width(2),
            channels = 2,
            rate = 44100,
            output = True
        )

        return out

    def play(self, gen, ns, voice_id, voice_index, loop=True):
        sys.path.insert(0, os.getcwd())
        gen = __import__(gen)
        midi_devices = {}

        if hasattr(gen, 'midi'):
            for device, device_id in gen.midi.iteritems():
                try:
                    midi_devices[device] = MidiManager(device_id, ns)
                except:
                    dsp.log('Could not load midi device %s with id %s' % (device, device_id))

        param_manager = ParamManager(ns)

        if audio_engine == 'alsa':
            out = self.open_alsa_pcm(ns.device)
        elif audio_engine == 'portaudio':
            out = self.open_pyaudio_pcm(ns.device)
        else:
            print 'Playback is disabled.'
            return False

        def dsp_loop(out, play, midi_devices, param_manager, voice_id, group):
            try:
                os.nice(-2)
            except OSError:
                os.nice(0)

            meta = {
                'midi': midi_devices,
                'param': param_manager,
                'id': voice_id,
                'group': group
            }

            snd = play(meta)
            snd = dsp.split(snd, 500)
            for s in snd:
                try:
                    out.write(s)
                except AttributeError:
                    dsp.log('Could not write to audio device')
                    return False

        while True:
            reload(gen)
            
            group = None
            if hasattr(gen, 'groups'):
                group = gen.groups[ voice_index % len(gen.groups) ]

            playback = mp.Process(target=dsp_loop, args=(out, gen.play, midi_devices, param_manager, voice_id, group))
            playback.start()
            playback.join()

            if not loop:
                break

        return True
