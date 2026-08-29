# fl_stubs.py
# Dummy shim for all FL Studio API modules so the script can be edited
# and imported outside FL Studio without crashing.

try:
    import arrangement  # type: ignore
    import channels  # type: ignore
    import device  # type: ignore
    import general  # type: ignore
    import launchMapPages  # type: ignore
    import midi  # type: ignore
    import mixer  # type: ignore
    import patterns  # type: ignore
    import playlist  # type: ignore
    import plugins  # type: ignore
    import transport  # type: ignore
    import ui  # type: ignore
except ImportError:  # Local editing fallback outside FL Studio.

    class _DummyArrangement:
        pass

    class _DummyChannels:
        def selectedChannel(self, *args):
            return -1

        def channelNumber(self, *args):
            return -1

        def channelCount(self, *args):
            return 0

        def getChannelColor(self, *args):
            return 0

        def getChannelName(self, *args):
            return ""

        def isChannelMuted(self, *args):
            return 0

        def muteChannel(self, *args):
            return None

        def getGridBit(self, *args):
            return 0

        def setGridBit(self, *args):
            return None

        def getChannelVolume(self, *args):
            return 0.8

        def setChannelVolume(self, *args):
            return None

        def getChannelPan(self, *args):
            return 0.0

        def setChannelPan(self, *args):
            return None

        def getStepParameterByIndex(self, *args):
            return 100

        def setStepParameterByIndex(self, *args):
            return None

        def midiNoteOn(self, *args):
            return None

    class _DummyDevice:
        def getName(self, *_args):
            return "Dummy FL Device"

        def getDeviceID(self, *_args):
            return b""

        def midiOutSysex(self, *_args):
            return None

        def midiOutMsg(self, *_args):
            return None

        def processMIDICC(self, *_args):
            return None

        def forwardMIDICC(self, *_args):
            return None

        def findEventID(self, *_args):
            return -1

        def getPortNumber(self, *_args):
            return 0

    class _DummyMixer:
        def automateEvent(self, *_args):
            return 0

        def getSongStepPos(self):
            return -1

        def getTrackCount(self): return 0
        def trackCount(self): return 2
        def getTrackColor(self, *_args): return 0
        def isTrackSelected(self, *_args): return False
        def setActiveTrack(self, *_args): return None
        def selectTrack(self, *_args): return None
        def isTrackArmed(self, *_args): return False
        def armTrack(self, *_args): return None

    class _DummyGeneral:
        def getRecPPB(self):
            return 4 * 96

        def getRecPPQ(self):
            return 96

        def processRECEvent(self, *_args):
            return 0

    class _DummyMidi:
        MIDI_NOTEON = 0x90
        MIDI_NOTEOFF = 0x80
        MIDI_CONTROLCHANGE = 0xB0
        REC_InvalidID = -1
        REC_UpdateValue = 1
        REC_UpdateControl = 32
        REC_FromMIDI = 64
        REC_SetChanged = 256
        REC_SetTouched = 512
        REC_MIDIController = 9216
        REC_Controller = 1024
        FromMIDI_Max = 1073741824
        MaxInt = 2147483647
        HW_Dirty_LEDs = 256
        PME_System = 2
        FPT_Play = 10
        FPT_Record = 12
        GC_Semitone = 1

        def EncodeRemoteControlID(self, port_num, chan_num, cc_num):
            return int(cc_num) + (int(chan_num) << 16) + ((int(port_num) + 1) << 22)

    class _DummyPlugins:
        def getParamCount(self, *_args):
            return 0

        def getParamName(self, *_args):
            return ""

        def getParamValue(self, *_args):
            return 0.0

        def setParamValue(self, *_args):
            return None

        def getPluginName(self, *_args):
            return ""

        def getPadInfo(self, *_args):
            raise RuntimeError("plugins module unavailable")

    class _DummyTransport:
        def globalTransport(self, *_args):
            return None

        def isRecording(self):
            return 0

        def isPlaying(self):
            return 0

    class _DummyPlaylist:
        _track_names: dict = {}

        def getTrackName(self, index):
            return self._track_names.get(index, f"Track {index}")

        def setTrackName(self, index, name):
            self._track_names[index] = name

        def getPerformanceModeState(self):
            return 0

        def getTrackCount(self):
            return 0

        def trackCount(self):
            return 0

        def getLiveBlockStatus(self, *_args):
            return 0

        def getLiveBlockColor(self, *_args):
            return 0

        def triggerLiveClip(self, *_args):
            return None

        def getDisplayZone(self):
            return 0

        def lockDisplayZone(self, *_args):
            return None

        def liveDisplayZone(self, *_args):
            return None

    class _DummyPatterns:
        def patternNumber(self, *args):
            return 1

    class _DummyLaunchMapPages:
        def createOverlayMap(self, *_args):
            return None

        def setMapItemTarget(self, *_args):
            return None

        def init(self, *_args):
            return None

        def updateMap(self, *_args):
            return None

        def getMapCount(self, *_args):
            return 0

        def getMapItemChannel(self, *_args):
            return -1

        def processMapItem(self, *_args):
            return None

    class _DummyUi:
        def showWindow(self, *_args):
            return None

        def setFocused(self, *_args):
            return None

        def scrollWindow(self, *_args):
            return None

        def getFocused(self, *_args):
            return 0

        def getFocusedFormID(self):
            return -1

        def getFocusedPluginName(self):
            return ""

        def crDisplayRect(self, *_args):
            return None

    arrangement = _DummyArrangement()
    channels = _DummyChannels()
    device = _DummyDevice()
    general = _DummyGeneral()
    launchMapPages = _DummyLaunchMapPages()
    midi = _DummyMidi()
    mixer = _DummyMixer()
    patterns = _DummyPatterns()
    playlist = _DummyPlaylist()
    plugins = _DummyPlugins()
    transport = _DummyTransport()
    ui = _DummyUi()
# ~gargoyles rule~
