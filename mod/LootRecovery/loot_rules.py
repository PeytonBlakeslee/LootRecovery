"""Intrinsic recovery categories, distinct from automatic-pickup policy."""
from __future__ import annotations

GEAR = {'weapon', 'shield', 'grenade', 'classmod', 'relic'}
GEAR_CLASSES = {'weapontypedefinition': 'weapon', 'turretweapontypedefinition': 'weapon', 'vehicleweapontypedefinition': 'weapon', 'buzzaxeweapontypedefinition': 'weapon', 'shielddefinition': 'shield', 'grenademoddefinition': 'grenade', 'classmoddefinition': 'classmod', 'crossdlcclassmoddefinition': 'classmod', 'artifactdefinition': 'relic'}
RARITY_MIN = {'Any': 0, 'Green+': 2, 'Blue+': 3, 'Purple+': 4, 'Legendary+': 5}
ECHO_TOKENS = ('echo', 'echorecorder', 'audio_log', 'audiolog')

# Specific TPS consumables observed in the local game object catalog.
TPS_CONSUMABLES = frozenset({
    'gd_buffdrinks.a_item.buffdrink_healinginstant',
    'gd_buffdrinks.a_item.buffdrink_healingregen',
    'gd_buffdrinks.a_item.buffdrink_oxygeninstant',
    'gd_buffdrinks.a_item.buffdrink_toughness',
})

UNCLASSIFIED = object()


def classify_path(name):
    name = name.lower()
    if not name or any(token in name for token in ECHO_TOKENS):
        return None
    if name.startswith('gd_ammodrops.'):
        return 'ammo'
    if name.startswith('gd_currency.a_item.'):
        return 'currency'
    if ('torguetoken' in name and 'pickup_torguetoken' in name
            or 'seraphcrystal' in name and 'pickup_seraphcrystal' in name):
        return 'currency'
    return UNCLASSIFIED


def classify_intrinsic(name, class_name):
    kind = classify_path(name)
    if kind is not UNCLASSIFIED:
        return kind
    if class_name.lower() == 'missionitemdefinition':
        return None
    return GEAR_CLASSES.get(class_name.lower(), UNCLASSIFIED)


def classify_definition(name, class_name, automatically_pickup=False):
    kind = classify_intrinsic(name, class_name)
    return kind if kind is not UNCLASSIFIED else ('other' if automatically_pickup else None)
