"""Regression checks for channel interpretation and DAW export; no audio device needed."""
import ast
import hashlib
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'py'))
from app import binaural as b, downmix as d, region_export as e


def service_functions():
    tree = ast.parse((ROOT / 'src-tauri/python/sf_waveform_service.py').read_text(encoding='utf-8'))
    names = {'_region', '_display_roles', '_order_choices'}
    ns = dict(math=math, crop_wav_region=e.crop_wav_region, render_speed_wav=e.render_speed_wav,
              _PRESET_WAVE_ORDER=b._PRESET_WAVE_ORDER, _PRESET_FILM_ORDER=b._PRESET_FILM_ORDER,
              _PRESET_SMPTE_ORDER=b._PRESET_SMPTE_ORDER, _FIVE_POINT={'quad','5.0','5.1'},
              _DISPLAY_ROLE={}, _SMPTE_EQUALS_WAVE={'quad','lcr','5.0','5.1'})
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[]), '<service functions>', 'exec'), ns)
    return ns


class AudioRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='soundfield_regression_')
        self.root = Path(self.temp.name)
        self.original_temp = e.TEMP_DIR
        e.TEMP_DIR = self.root / 'exports'
        e._speed_claims.clear()

    def tearDown(self):
        e.TEMP_DIR = self.original_temp
        self.temp.cleanup()

    def wav(self, name='source.wav', ch=5, mask=None, roles=None, level=.02, subtype='FLOAT'):
        path = self.root / name
        sf.write(path, np.tile(np.arange(1,ch+1)*level, (1024,1)), 48000,
                 subtype=subtype, format='WAVEX' if mask is not None else 'WAV')
        if mask is not None:
            self.assertTrue(e._patch_channel_mask(str(path),mask))
        if roles:
            xml='<BWFXML><TRACK_LIST>'+''.join(f'<TRACK><INTERLEAVE_INDEX>{i+1}</INTERLEAVE_INDEX><NAME>{role}</NAME></TRACK>' for i,role in enumerate(roles))+'</TRACK_LIST></BWFXML>'
            with path.open('r+b') as f:
                f.seek(0,2); e._write_chunk(f,b'iXML',xml.encode()); size=f.tell()
                f.seek(4); f.write(struct.pack('<I',size-8))
        return str(path)

    def test_custom_ixml_preserved(self):
        p=self.wav(roles=['C','L','R','Ls','Rs'])
        layout=b.resolve_layout(p,5)
        self.assertEqual(layout.channel_order,'custom')
        self.assertEqual(layout.azimuth,(0,30,-30,110,-110))
        self.assertEqual(b.layout_channel_roles(layout),('C','L','R','LB','RB'))

    def test_manual_quad_beats_filename(self):
        for name in ['source_ambix.wav','source_fuma.wav','source_ambisonic.wav','source_AMBEO.wav']:
            with self.subTest(name=name):
                layout=b.resolve_layout(name,4,'quad',inspect_file=False)
                self.assertEqual((layout.topology,layout.preset),('surround','quad'))

    def test_side_masks_and_invalid_61_order(self):
        for ch,mask,preset in [(5,0x607,'5.0'),(6,0x60f,'5.1')]:
            self.assertEqual(b.resolve_layout('x.wav',ch,channel_mask=mask,inspect_file=False).preset,preset)
        self.assertFalse(b.resolve_layout('x.wav',7,'6.1|film',inspect_file=False).can_auto_play)
        self.assertEqual(b.resolve_layout('x.wav',7,'6.1|wave',inspect_file=False).preset,'6.1')

    def test_high_order_ambix_auto(self):
        # 16·25·36ch 는 스피커 배치와 겹치지 않아 AmbiX 로 자동 확정 (2026-09-18). 9ch 는 7.0.2 와 겹쳐 여전히 묻는다.
        for ch,order in [(16,3),(25,4),(36,5)]:
            layout=b.resolve_layout('plain.wav',ch,inspect_file=False)
            self.assertEqual((layout.topology,layout.preset,layout.source_order,layout.source,layout.can_auto_play),
                             ('ambisonic','ambix',order,'channels',True),ch)
        nine=b.resolve_layout('plain.wav',9,inspect_file=False)
        self.assertFalse(nine.can_auto_play); self.assertEqual(nine.candidates,('7.0.2','ambix'))
        self.assertFalse(b.resolve_layout('field_AmbiX.wav',12,inspect_file=False).can_auto_play)   # 제곱수 아님

    def test_smpte_71(self):
        layout=b.resolve_layout('x.wav',8,'7.1|smpte',inspect_file=False)
        self.assertEqual(b.layout_channel_roles(layout),('L','R','C','LFE','LS','RS','LB','RB'))
        self.assertEqual([x['order'] for x in service_functions()['_order_choices']('7.1')],['wave','film','smpte'])

    def test_masks_and_samples_preserved(self):
        for ch,mask in [(5,0x37),(6,0x3f),(8,0x63f),(5,0),(5,None)]:
            for subtype in ['PCM_24','FLOAT']:
                with self.subTest(ch=ch,mask=mask,subtype=subtype):
                    p=self.wav(f'{ch}_{mask}_{subtype}.wav',ch,mask,subtype=subtype)
                    before=hashlib.sha256(Path(p).read_bytes()).digest()
                    result=e.render_speed_wav(p,.5)
                    self.assertIsNotNone(result)
                    self.assertEqual(e._source_channel_mask(result),mask)
                    data,sr=sf.read(result,always_2d=True)
                    self.assertEqual(data.shape,(2048,ch))
                    np.testing.assert_allclose(data[0],np.arange(1,ch+1)*.02,atol=2e-7)
                    self.assertEqual(hashlib.sha256(Path(p).read_bytes()).digest(),before)

    def test_source_replacement_invalidates_speed_and_crop(self):
        p=self.wav(); first_crop=e.crop_wav_region(p,0,.01)
        first=e.render_speed_wav(p,.5)
        first_bytes=Path(first).read_bytes()
        stamp=os.stat(p)
        self.wav(level=.04)
        os.utime(p,ns=(stamp.st_atime_ns,stamp.st_mtime_ns+1000000000))
        second=e.render_speed_wav(p,.5)
        self.assertNotEqual(first,second)
        self.assertEqual(Path(first).read_bytes(),first_bytes)
        self.assertAlmostEqual(sf.read(second)[0][0,0],.04,places=6)
        self.assertNotEqual(first_crop,e.crop_wav_region(p,0,.01))
        self.assertEqual(second,e.render_speed_wav(p,.5))

    def test_mask_failure_not_exported_or_cached(self):
        p=self.wav(mask=0x37)
        with patch.object(e,'_patch_channel_mask',return_value=False):
            self.assertIsNone(e.render_speed_wav(p,.5))
        self.assertFalse(e._speed_claims)
        self.assertEqual(list(e.TEMP_DIR.glob('*.wav')),[])

    def test_export_failure_never_returns_source(self):
        ns=service_functions()
        ns['crop_wav_region']=lambda *args:None
        ns['render_speed_wav']=lambda *args:None
        self.assertEqual(ns['_region']('original.wav',None,None,1),{'path':'original.wav'})
        for args in [(0,1,1),(None,None,.5),(None,None,0),(None,None,float('nan')),(0,None,1)]:
            with self.subTest(args=args), self.assertRaises((RuntimeError,ValueError)):
                ns['_region']('original.wav',*args)

    def test_headroom_not_cancelled_and_sidecar_found(self):
        # 정책(2026-09-16): 바이노럴 = 접기 기준 +9.6 dB + 그 배치의 일반 재생 감쇠(ffmpeg normalize).
        # 5.1 은 -7.66 dB → +1.94 dB. 순 이득 0.37*10^(1.94/20)=0.46 < 1 (보호 감쇠 전부 상쇄 X).
        roles51=('L','R','C','LFE','LB','RB')
        off=d.norm_db(roles51,6)
        self.assertAlmostEqual(off,20*math.log10(1/2.414),5)
        self.assertAlmostEqual(b.sidecar_volume_db(1),9.6)
        self.assertAlmostEqual(b.sidecar_volume_db(1,off),9.6+off)
        self.assertAlmostEqual(b.sidecar_volume_db(.5,off),9.6+off-6.020599913)
        self.assertLess(b.SIDECAR_OUTPUT_HEADROOM_GAIN*10**((9.6+off)/20),1.0)
        # 일반 재생 다운믹스: 풀스케일 입력을 접어도 1.0 을 못 넘는다 (리미터 없음)
        self.assertLessEqual(max(d.row_sums(roles51))*d.norm_gain(roles51,6),1.0+1e-9)
        self.assertAlmostEqual(d.norm_db(('L','R','C','LFE','LS','RS','LB','RB'),8),20*math.log10(1/3.121),5)
        self.assertAlmostEqual(d.norm_db((),4),20*math.log10(1/1.707),5)   # 역할 모를 때 옛 식 합
        with patch.dict(os.environ,{'SOUNDFIELD_BINAURAL_SIDECAR':''}):
            self.assertEqual(Path(b.locate_sidecar()).resolve(),(ROOT/'py/sidecar/scsearch-monitor-fixed.exe').resolve())

    def test_rate_restores_position_and_transport(self):
        tree=ast.parse((ROOT/'py/app/ui/playback.py').read_text(encoding='utf-8'))
        method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='setPlaybackRate')
        class PS: PlayingState='playing'
        class URL:
            @staticmethod
            def fromLocalFile(p): return p
        ns={'PS':PS,'QUrl':URL}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'<rate>','exec'),ns)
        class Sink:
            def set_rate(self,r): pass
            def setPlaybackRate(self,r): pass
        class Player:
            _rate=1.; _mode='binaural'; _path='source.wav'; _pending_play=False
            _eng=Sink(); _qt=Sink(); enabled=True; transport='paused'; loads=0
            def _fallback_from_binaural(self,reason): self._mode='engine'
            def _should_use_binaural(self,path): return self.enabled
            def position(self): return 3456
            def playbackState(self): return self.transport
            def setSource(self,path):
                self.loads+=1; self._mode='binaural'; self._pending_play=False; self._pending_position_ms=0
        for transport in ['playing','paused']:
            p=Player(); p.transport=transport
            ns['setPlaybackRate'](p,.5); ns['setPlaybackRate'](p,1.)
            self.assertEqual(p._mode,'binaural')
            self.assertEqual(p._pending_position_ms,3456)
            self.assertEqual(p._pending_play,transport=='playing')
            ns['setPlaybackRate'](p,1.); self.assertEqual(p.loads,1)
        p=Player(); p.enabled=False
        ns['setPlaybackRate'](p,.5); ns['setPlaybackRate'](p,1.)
        self.assertEqual(p.loads,0)

if __name__=='__main__': unittest.main()
