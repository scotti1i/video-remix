"""首帧路线离线合同/命令及真实图片探测测试；不调用生成账号。"""

import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from test_bootstrap import bootstrap
from test_creative import plan
from video_remix.cli import doctor
from video_remix.creative import assemble, compile_plan
from video_remix.dreamina import Dreamina, validate_capabilities, validate_media
from video_remix.project import add_asset, add_variant, initialize
from video_remix.storage import atomic, read
from video_remix.variation import prepare_variation


def png(width, height):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    header = struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)
    pixels = (b'\0' + b'\x20\x40\x60' * width) * height
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


class FirstFrameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        initialize(self.root, 'first-frame offline test')
        image = Path(self.temp.name) / 'anchor.png'
        image.write_bytes(png(720, 1280))
        self.asset = add_asset(self.root, 'product', image, '首帧画面')
        self.plan = {**plan(), 'generation_route': 'first_frame'}
        self.spec = assemble(self.plan)
        self.assets = read(self.root / 'project.json')['assets']
        with patch('video_remix.dreamina.shutil.which', return_value='dreamina'):
            self.provider = Dreamina()

    def register(self, value):
        file = Path(self.temp.name) / 'plan.json'
        atomic(file, value)
        return compile_plan(self.root, file)

    def test_optional_route_is_preserved_without_changing_legacy_prompt(self):
        legacy = assemble(plan())
        self.assertNotIn('generation_route', legacy)
        self.assertEqual(self.spec['prompt'], legacy['prompt'])
        self.assertEqual(self.spec['creative_mode'], 'image_text')
        result = self.register(self.plan)
        self.assertEqual(read(Path(result['spec']))['generation_route'], 'first_frame')
        self.assertEqual(read(self.root / 'variants/sample/spec.json'), read(Path(result['spec'])))

    def test_first_frame_has_one_image_and_no_ratio_without_rewriting_prompt(self):
        args = self.provider.arguments(self.spec, self.root, self.assets)
        self.assertEqual(args[1], 'image2video')
        self.assertEqual(args.count('--image'), 1)
        self.assertNotIn('--ratio', args)
        self.assertEqual(args[args.index('--prompt') + 1], self.spec['prompt'])
        self.assertEqual(args[args.index('--model_version') + 1], 'seedance2.0fast_vip')
        self.assertEqual(args[-2:], ['--poll', '0'])
        for value in (assemble(plan()), {**assemble(plan()), 'generation_route': 'reference'}):
            old = self.provider.arguments(value, self.root, self.assets)
            expected = ['dreamina', 'multimodal2video', '--image', str((self.root / self.asset['path']).resolve()),
                        '--prompt', value['prompt'], '--duration', '15', '--ratio', '9:16',
                        '--video_resolution', '720p', '--model_version', 'seedance2.0fast_vip', '--poll', '0']
            self.assertEqual(old, expected)

    def test_invalid_routes_and_extra_inputs_fail_before_registration(self):
        changes = [{'generation_route': value} for value in ('unknown', '', None, [])]
        changes += [{'inputs': []}, {'inputs': self.plan['inputs'] * 2}]
        changes += [{'inputs': [{'type': kind, 'asset': 'product', 'role': 'invalid'}]}
                    for kind in ('video', 'audio')]
        changes += [{'mode': 'image_audio'}, {'mode': 'video_reference'}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                assemble({**self.plan, **change})
        file = Path(self.temp.name) / 'spec.json'
        atomic(file, {**self.spec, 'generation_route': 'typo'})
        with self.assertRaises(ValueError):
            add_variant(self.root, file)
        self.assertFalse((self.root / 'variants').exists())

    def test_new_route_does_not_open_unadapted_model_ranges(self):
        for model in ('seedance1.0fast', 'seedance1.5pro', 'seedance2.0mini', 'seedance2.5'):
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, '尚未适配'):
                validate_capabilities({**self.spec, 'model': model}, model)
        for duration in (3, 16):
            with self.assertRaises(ValueError):
                validate_capabilities({**self.spec, 'duration': duration}, self.spec['model'])
        with self.assertRaises(ValueError):
            validate_capabilities(self.spec, 'a different supported model')
        validate_capabilities(self.spec, self.spec['model'])

    @unittest.skipUnless(shutil.which('ffprobe'), 'requires existing ffprobe')
    def test_real_image_dimensions_are_checked_without_resizing(self):
        validate_media(self.spec, self.root, self.assets)
        before = (self.root / self.asset['path']).read_bytes()
        with self.assertRaisesRegex(ValueError, '实际画幅'):
            validate_media({**self.spec, 'ratio': '16:9'}, self.root, self.assets)
        self.assertEqual((self.root / self.asset['path']).read_bytes(), before)

    def test_video_disguised_as_image_and_rotation_are_rejected(self):
        stream = {'codec_type': 'video', 'width': 90, 'height': 160, 'sample_aspect_ratio': '1:1'}
        cases = [({'format_name': 'mov,mp4,m4a,3gp,3g2,mj2'}, stream),
                 ({'format_name': 'png_pipe'}, {**stream, 'tags': {'rotate': '90'}}),
                 ({'format_name': 'png_pipe'}, {**stream, 'sample_aspect_ratio': '2:1'})]
        for container, video in cases:
            with self.subTest(video=video), patch('video_remix.dreamina.command',
                    return_value={'streams': [video], 'format': container}):
                with self.assertRaises(ValueError):
                    validate_media(self.spec, self.root, self.assets)

    def test_batch_preflight_reads_each_actual_route_once(self):
        specs = [self.spec, assemble(plan()), {**self.spec, 'id': 'second'}]
        items = [(self.root / 'variants' / s['id'], s, self.assets, None, []) for s in specs]
        response = subprocess.CompletedProcess([], 0, 'seedance2.0fast_vip', '')
        with patch('video_remix.dreamina.shutil.which', return_value='ffprobe'), \
                patch('video_remix.dreamina.subprocess.run', return_value=response) as call, \
                patch('video_remix.dreamina.validate_media') as media:
            self.provider.preflight(items)
        self.assertEqual([c.args[0][1] for c in call.call_args_list], ['image2video', 'multimodal2video'])
        self.assertEqual(media.call_count, 3)

    def test_vary_preserves_route_and_rerun_cannot_change_it(self):
        self.register(self.plan)
        control = {'format': 'video-remix-variation.v1', 'id': 'child', 'parent': 'sample',
                   'frozen_core': {'mechanism': 'same motion', 'product_assets': ['product']},
                   'slots': {'scene': {'text': [{'path': 'look', 'from': self.plan['look'], 'to': 'new workshop'}]}}}
        child, report = prepare_variation(self.root, control)
        self.assertEqual(child['generation_route'], 'first_frame')
        self.assertFalse(any(c['path'] == 'generation_route' for c in report['changes']))
        same = {**self.spec, 'id': 'same', 'kind': 'rerun', 'parent': 'sample'}
        file = Path(self.temp.name) / 'rerun.json'
        atomic(file, same)
        add_variant(self.root, file)
        for route in ('reference', None):
            changed = {**same, 'id': 'changed', 'generation_route': route}
            if route is None:
                changed.pop('generation_route')
            atomic(file, changed)
            with self.assertRaisesRegex(ValueError, 'generation_route'):
                add_variant(self.root, file)

    def test_read_only_capability_report_is_explicit(self):
        report = doctor()
        self.assertEqual(report['generation_routes'], ['reference', 'first_frame'])
        self.assertFalse(report['account_checked'])

    def test_legacy_and_explicit_reference_are_same_rerun_conditions(self):
        self.register(plan())
        for name, route in (('explicit', 'reference'), ('implicit', None)):
            spec = {**assemble(plan()), 'id': name, 'kind': 'rerun', 'parent': 'sample'}
            if route:
                spec['generation_route'] = route
            file = Path(self.temp.name) / f'{name}.json'
            atomic(file, spec)
            add_variant(self.root, file)


class FirstFrameUpgradeGuardTests(unittest.TestCase):
    def test_first_frame_input_is_not_sent_to_legacy_compiler(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'first.json'
            atomic(file, {'generation_route': 'first_frame'})
            old = subprocess.CompletedProcess([], 0, json.dumps({'video_adapters': ['dreamina']}), '')
            for args in (['compile', str(file)], ['--project', str(root), 'variant', str(file)],
                         [f'--project={root}', 'compile', str(file)],
                         ['compile', '--', str(file)], ['variant', '--', str(file)]):
                with self.subTest(args=args), patch.object(bootstrap.subprocess, 'run', return_value=old):
                    with self.assertRaisesRegex(RuntimeError, '未显式支持'):
                        bootstrap.guard_incoming_route(root / 'old-engine', args)
            atomic(file, {'prompt': 'old reference spec'})
            with patch.object(bootstrap.subprocess, 'run') as probe:
                bootstrap.guard_incoming_route(root / 'old-engine', ['variant', str(file)])
            probe.assert_not_called()

    def test_old_target_ignoring_standalone_spec_cannot_claim_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic(root / 'variants/new/spec.json', {'generation_route': 'first_frame'})
            old = subprocess.CompletedProcess([], 0, json.dumps({'compatible': True}), '')
            with patch.object(bootstrap.subprocess, 'run', return_value=old):
                with self.assertRaisesRegex(RuntimeError, '未显式支持'):
                    bootstrap.compatibility(root / 'old-engine', root)
            supported = {"compatible": True, "generation_routes": ['reference', 'first_frame']}
            with patch.object(bootstrap.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(supported), '')):
                self.assertEqual(bootstrap.compatibility(root / 'new-engine', root), supported)

    def test_legacy_project_does_not_require_new_capability_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic(root / 'variants/old/spec.json', {'prompt': 'legacy'})
            old = subprocess.CompletedProcess([], 0, json.dumps({'compatible': True}), '')
            with patch.object(bootstrap.subprocess, 'run', return_value=old):
                self.assertTrue(bootstrap.compatibility(root / 'old-engine', root)['compatible'])

    def test_uncompiled_first_frame_plan_also_blocks_old_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic(root / 'plans/new/plan.json', {'generation_route': 'first_frame'})
            old = subprocess.CompletedProcess([], 0, json.dumps({'compatible': True}), '')
            with patch.object(bootstrap.subprocess, 'run', return_value=old):
                with self.assertRaisesRegex(RuntimeError, '未显式支持'):
                    bootstrap.compatibility(root / 'old-engine', root)
