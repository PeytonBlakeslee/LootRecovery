"""REF-11: production LootRecovery defaults, reset debounce, save and redraw.

No UnrealSDK imports, actual player settings paths or hooks. Only selected
production assignment/function nodes are executed with controlled boundary stubs.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

SOURCE=Path(__file__).resolve().parents[1]/'mod'/'LootRecovery'/'__init__.py'
FILTERS=('Ammo','Currency','Other','Weapons','Shields','Grenades','ClassMods','Relics')
ALL_OPTIONS=(*FILTERS,'Containers','SelfDropped','Feedback','Rarity')
DEFAULTS={**{key:True for key in FILTERS},'Containers':False,'SelfDropped':False,
          'Feedback':False,'Rarity':'Any'}

class Option:
    def __init__(self,*args,**kwargs):
        self.identifier = str(args[0]) if args else kwargs.get('identifier','')
        self.value = args[1] if len(args)>1 else kwargs.get('value')
        self.kwargs = kwargs

class Clock:
    def __init__(self): self.value=100.0
    def monotonic(self): return self.value

def get_nodes():
    return ast.parse(SOURCE.read_text(encoding='utf-8-sig'), filename=str(SOURCE)).body

def fresh(*,is_tps=False):
    requested=set(ALL_OPTIONS)|{'CATEGORY_OPTIONS'}
    selected=[]
    for node in get_nodes():
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            if node.targets[0].id in requested:selected.append(node)
    present={node.targets[0].id for node in selected}
    if present!=requested:raise AssertionError(f'LootRecovery option shape changed: missing {requested-present}')
    env={'BoolOption':Option,'SpinnerOption':Option,'IS_TPS':is_tps}
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(SOURCE),'exec'),env)
    return env

def harness(*,is_tps=False):
    env=fresh(is_tps=is_tps)
    fns=[n for n in get_nodes() if isinstance(n,ast.FunctionDef) and n.name=='on_reset']
    if len(fns)!=1:raise AssertionError('Expected one LootRecovery on_reset')
    clock=Clock(); actions=[]
    menu=ModuleType('willow2_mod_menu')
    reload_fn=Mock(side_effect=lambda: actions.append('redraw'))
    menu.options_menu=SimpleNamespace(dangerous_reload_current_page=reload_fn)
    runtime=SimpleNamespace(reset_at=0.0,enabled=False,_state='idle')
    mod=SimpleNamespace(is_enabled=False,save_settings=Mock(side_effect=lambda: actions.append('save')))
    env.update({'time':clock,'runtime':runtime,'MOD':mod,'RESET_COOLDOWN':0.5,
                'TELEPORT':SimpleNamespace(key='Enter'), 'log':Mock(), 'error':Mock()})
    exec(compile(ast.Module(body=fns,type_ignores=[]),str(SOURCE),'exec'),env)
    return SimpleNamespace(env=env,runtime=runtime,mod=mod,clock=clock,actions=actions,
                           menu=menu,redraw_fn=reload_fn,
                           options={key:env[key] for key in ALL_OPTIONS})

class LootRecoveryResetContractTests(unittest.TestCase):
    def test_fresh_constructor_defaults_bl2_and_tps(self):
        for is_tps in (False,True):
            with self.subTest(is_tps=is_tps):
                env=fresh(is_tps=is_tps)
                self.assertEqual({k:env[k].value for k in ALL_OPTIONS},DEFAULTS)
                self.assertEqual(set(env['CATEGORY_OPTIONS']), {'ammo','currency','other','weapon','shield','grenade','classmod','relic'})
        self.assertNotEqual(fresh()['Relics'].kwargs['display_name'],fresh(is_tps=True)['Relics'].kwargs['display_name'])

    def test_reset_restores_all_fields_and_gather_key_once(self):
        h=harness()
        for key,opt in h.options.items(): opt.value='Legendary+' if key=='Rarity' else not DEFAULTS[key]
        h.env['TELEPORT'].key='F9'
        h.runtime.enabled=True
        with patch.dict(sys.modules, {'willow2_mod_menu':h.menu}): h.env['on_reset'](None)
        self.assertEqual({k:v.value for k,v in h.options.items()},DEFAULTS)
        self.assertEqual(h.env['TELEPORT'].key,'Enter')
        self.assertEqual(h.actions,['save','redraw'])
        self.assertTrue(h.runtime.enabled)
        self.assertEqual(h.runtime._state,'idle')

    def test_reset_debounce_and_exact_boundary(self):
        h=harness()
        with patch.dict(sys.modules, {'willow2_mod_menu':h.menu}):
            h.env['on_reset'](None)
            h.clock.value=100.499
            h.options['Ammo'].value=False
            h.env['on_reset'](None)
            self.assertFalse(h.options['Ammo'].value)
            self.assertEqual(h.mod.save_settings.call_count,1)
            h.clock.value=100.5
            h.env['on_reset'](None)
        self.assertTrue(h.options['Ammo'].value)
        self.assertEqual(h.mod.save_settings.call_count,2)

    def test_no_mod_still_resets_state_and_redraws(self):
        h=harness()
        h.env['MOD']=None
        h.options['Containers'].value=True
        with patch.dict(sys.modules, {'willow2_mod_menu':h.menu}):h.env['on_reset'](None)
        self.assertFalse(h.options['Containers'].value)
        self.assertEqual(h.actions,['redraw'])

    def test_legacy_list_refresh_fallback(self):
        h=harness()
        legacy=SimpleNamespace(Refresh=Mock())
        h.menu.options_menu=SimpleNamespace(latest_list=lambda:legacy)
        with patch.dict(sys.modules, {'willow2_mod_menu':h.menu}): h.env['on_reset'](None)
        legacy.Refresh.assert_called_once()
        h.mod.save_settings.assert_called_once()

    def test_missing_menu_does_not_block_option_reset(self):
        h=harness()
        h.options['Feedback'].value=True
        with patch.dict(sys.modules, {'willow2_mod_menu':None}):h.env['on_reset'](None)
        self.assertFalse(h.options['Feedback'].value)
        self.assertEqual(h.actions,['save'])

    def test_save_failure_reports_but_refreshes_and_retains_defaults(self):
        h=harness()
        h.options['Weapons'].value=False
        h.mod.save_settings.side_effect=OSError('injected failure')
        with patch.dict(sys.modules, {'willow2_mod_menu':h.menu}):h.env['on_reset'](None)
        self.assertTrue(h.options['Weapons'].value)
        h.env['error'].assert_called_once()
        h.redraw_fn.assert_called_once()

    def test_reset_callback_is_connected_to_menu_and_keybind(self):
        tree=get_nodes()
        reset=[n for n in tree if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='RESET' for t in n.targets)]
        teleport=[n for n in tree if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TELEPORT' for t in n.targets)]
        mod=[n for n in tree if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='MOD' for t in n.targets) and isinstance(n.value,ast.Call)]
        self.assertEqual(len(reset),1)
        self.assertEqual(len(teleport),1)
        self.assertEqual(len(mod),1)
        self.assertIn('on_press=on_reset',ast.unparse(reset[0]))
        self.assertIn('"Enter"',ast.unparse(teleport[0]).replace("'Enter'",'"Enter"'))
        for token in ('RESET','TELEPORT'):
            self.assertIn(token,ast.unparse(mod[0]))

if __name__=='__main__':unittest.main()
