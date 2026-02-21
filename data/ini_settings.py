# ARK ASA Manager – INI settings definitions
# =============================================================================
#
# Each setting tuple:
#   (ini_file, section, key, label, description, value_type, default, min, max[, step])
#
# ini_file: "gus" = GameUserSettings.ini, "game" = Game.ini
# section: INI section name
# value_type: "bool", "float", "int"

_STAT_NAMES = [
    "Health", "Stamina", "Torpidity", "Oxygen", "Food",
    "Water", "Temperature", "Weight", "Damage", "Speed",
]

_SS = "ServerSettings"
_GM = "/Script/ShooterGame.ShooterGameMode"

# =============================================================================
# Base setting groups
# =============================================================================

INI_SETTINGS_GENERAL: list = [
    ("gus", _SS, "serverPVE", "PvE Mode", "Enable Player vs Environment mode.", "bool", "False", 0, 1),
    ("gus", _SS, "DifficultyOffset", "Difficulty Offset", "Base difficulty (0.0–1.0). Affects creature levels.", "float", "1.0", 0.0, 1.0, 0.1),
    ("gus", _SS, "OverrideOfficialDifficulty", "Override Official Difficulty", "Set to 5.0 to allow wild creatures up to level 150.", "float", "0.0", 0.0, 15.0, 0.5),
    ("gus", _SS, "AllowCaveBuildingPvE", "Allow Cave Building (PvE)", "Allow structures inside caves in PvE.", "bool", "False", 0, 1),
    ("gus", _SS, "AllowCaveBuildingPvP", "Allow Cave Building (PvP)", "Allow structures inside caves in PvP.", "bool", "True", 0, 1),
    ("gus", _SS, "DayCycleSpeedScale", "Day Cycle Speed", "Overall day/night cycle speed. Lower = longer days.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "DayTimeSpeedScale", "Daytime Speed", "Speed of daytime relative to night.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "NightTimeSpeedScale", "Nighttime Speed", "Speed of nighttime relative to day.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "ShowMapPlayerLocation", "Show Map Player Location", "Show player position on the in-game map.", "bool", "True", 0, 1),
    ("gus", _SS, "AllowThirdPersonPlayer", "Allow Third Person", "Let players use 3rd-person camera.", "bool", "True", 0, 1),
    ("gus", _SS, "ServerCrosshair", "Server Crosshair", "Show crosshair for all players.", "bool", "True", 0, 1),
    ("gus", _SS, "AllowHitMarkers", "Allow Hit Markers", "Show hit markers on damage.", "bool", "True", 0, 1),
    ("gus", _SS, "AllowFlyerCarryPvE", "Flyer Carry (PvE)", "Allow flyers to pick up wild creatures in PvE.", "bool", "False", 0, 1),
    ("gus", _SS, "PreventOfflinePvP", "Offline Raid Prevention", "Tribes offline become invulnerable.", "bool", "False", 0, 1),
    ("gus", _SS, "ForceAllStructureLocking", "Force Structure Locking", "Default-lock all placed structures.", "bool", "False", 0, 1),
    ("gus", _SS, "EnablePvPGamma", "Enable PvP Gamma", "Allow gamma adjustment in PvP.", "bool", "False", 0, 1),
    ("gus", _SS, "DisablePvEGamma", "Disable PvE Gamma", "Prevent gamma command in PvE.", "bool", "False", 0, 1),
    ("gus", _SS, "PreventTribeAlliances", "Prevent Tribe Alliances", "Block tribes from creating alliances.", "bool", "False", 0, 1),
    ("gus", _SS, "PreventSpawnAnimations", "Skip Spawn Animations", "Skip wake-up animation on respawn.", "bool", "False", 0, 1),
    ("gus", _SS, "RandomSupplyCratePoints", "Random Supply Drops", "Randomise supply drop locations.", "bool", "False", 0, 1),
    ("gus", _SS, "DisableWeatherFog", "Disable Fog", "Remove weather fog effects.", "bool", "False", 0, 1),
    ("gus", _SS, "NonPermanentDiseases", "Non-Permanent Diseases", "Diseases are lost on respawn.", "bool", "False", 0, 1),
    ("gus", _SS, "globalVoiceChat", "Global Voice Chat", "Voice chat is heard server-wide.", "bool", "False", 0, 1),
    ("gus", _SS, "ProximityChat", "Proximity Chat", "Only nearby players see text chat.", "bool", "False", 0, 1),
    ("gus", _SS, "AutoSavePeriodMinutes", "Auto-Save Interval (min)", "Minutes between automatic world saves.", "float", "15.0", 1.0, 120.0, 1.0),
    ("gus", _SS, "MaxTamedDinos", "Max Tamed Dinos (Server)", "Global cap on tamed creatures.", "int", "5000", 0, 20000, 100),
    ("gus", _SS, "MaxPersonalTamedDinos", "Max Tamed Dinos (Tribe)", "Per-tribe creature cap (0 = unlimited).", "int", "0", 0, 5000, 50),
    ("game", _GM, "bUseSingleplayerSettings", "Use Singleplayer Settings", "Apply boosted SP multipliers (breeding, XP, etc.).", "bool", "False", 0, 1),
    ("game", _GM, "bDisableFriendlyFire", "Disable Friendly Fire", "Prevent damage to tribemates/tames/structures.", "bool", "False", 0, 1),
]

INI_SETTINGS_CROPS: list = [
    ("game", _GM, "CropGrowthSpeedMultiplier", "Crop Growth Speed", "Scales speed of crop growth in plots.", "float", "1.0", 0.01, 20.0),
    ("game", _GM, "CropDecaySpeedMultiplier", "Crop Decay Speed", "Scales speed of crop decay (higher = faster decay).", "float", "1.0", 0.01, 10.0),
    ("game", _GM, "GlobalSpoilingTimeMultiplier", "Global Spoiling Time", "Scales spoiling of perishables (higher = longer).", "float", "1.0", 0.01, 20.0),
    ("game", _GM, "GlobalItemDecompositionTimeMultiplier", "Item Decomposition Time", "Scales decomp time of dropped items/loot bags.", "float", "1.0", 0.01, 20.0),
]

INI_SETTINGS_DINOS: list = [
    # -- Combat & General --
    ("gus", _SS, "DinoDamageMultiplier", "Wild Dino Damage", "Scales damage dealt by wild creatures.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "DinoResistanceMultiplier", "Wild Dino Resistance", "Scales damage resistance of wild creatures.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "DinoCharacterFoodDrainMultiplier", "Dino Food Drain", "Scales how fast dinos consume food.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "DinoCharacterHealthRecoveryMultiplier", "Dino Health Recovery", "Scales passive health regen speed.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "DinoCharacterStaminaDrainMultiplier", "Dino Stamina Drain", "Scales stamina consumption rate.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "TamingSpeedMultiplier", "Taming Speed", "Higher = faster taming.", "float", "1.0", 0.1, 100.0, 1.0),
    ("gus", _SS, "PreventMateBoost", "Prevent Mate Boost", "Disable creature mate-boost buff.", "bool", "False", 0, 1),
    ("gus", _SS, "AllowAnyoneBabyImprintCuddle", "Anyone Can Imprint", "Any player can cuddle/imprint babies.", "bool", "False", 0, 1),
    ("gus", _SS, "DisableImprintDinoBuff", "Disable Imprint Buff", "Remove rider imprint stat bonus.", "bool", "False", 0, 1),
    ("gus", _SS, "AllowRaidDinoFeeding", "Allow Raid Dino Feeding", "Titanosaurs can be permanently fed.", "bool", "False", 0, 1),
    # -- Respawn & Population --
    ("gus", _SS, "DinoCountMultiplier", "Dino Count Multiplier",
     "Scales the number of wild dinos that spawn on the map. Higher = more wild creatures.", "float", "1.0", 0.01, 5.0),
    ("gus", _SS, "ServerAutoForceRespawnWildDinosInterval", "Auto Respawn Wild Dinos (s)",
     "Seconds between automatic forced respawns of all wild dinos. 0 = disabled. "
     "Helps refresh population on long-running servers.", "float", "0.0", 0.0, 86400.0, 60.0),
    # -- Breeding --
    ("game", _GM, "MatingIntervalMultiplier", "Mating Interval", "Scales time between matings (lower = faster).", "float", "1.0", 0.01, 10.0),
    ("game", _GM, "MatingSpeedMultiplier", "Mating Speed", "Scales how fast mating completes.", "float", "1.0", 0.1, 50.0),
    ("game", _GM, "EggHatchSpeedMultiplier", "Egg Hatch Speed", "Higher = eggs hatch faster.", "float", "1.0", 0.1, 100.0, 1.0),
    ("game", _GM, "BabyMatureSpeedMultiplier", "Baby Mature Speed", "Higher = babies grow faster.", "float", "1.0", 0.1, 200.0, 1.0),
    ("game", _GM, "BabyCuddleIntervalMultiplier", "Cuddle Interval",
     "Scales time between imprint care requests (cuddle, walk, food). "
     "Lower values = more frequent requests, faster imprinting.", "float", "1.0", 0.01, 10.0),
    ("game", _GM, "BabyFoodConsumptionSpeedMultiplier", "Baby Food Consumption",
     "Scales how fast baby dinos consume food from their inventory. "
     "Higher values = faster drain, requiring more food during raising.", "float", "1.0", 0.01, 10.0),
    ("game", _GM, "BabyImprintingStatScaleMultiplier", "Imprinting Stat Scale", "Scales stat bonus from imprinting.", "float", "1.0", 0.0, 10.0),
    ("game", _GM, "BabyImprintAmountMultiplier", "Imprint Amount",
     "Scales the imprint percentage gained per care event. Higher values mean fewer "
     "cuddle/care events needed for 100% imprint.", "float", "1.0", 0.1, 50.0, 1.0),
    ("game", _GM, "BabyCuddleGracePeriodMultiplier", "Cuddle Grace Period", "Time before imprint quality degrades.", "float", "1.0", 0.01, 10.0),
    ("game", _GM, "BabyCuddleLoseImprintQualitySpeedMultiplier", "Imprint Loss Speed", "Speed imprint quality drops after grace.", "float", "1.0", 0.01, 10.0),
    ("game", _GM, "LayEggIntervalMultiplier", "Lay Egg Interval", "Scales egg-laying frequency.", "float", "1.0", 0.01, 10.0),
    # -- Speed Leveling --
    ("game", _GM, "bAllowSpeedLeveling", "Allow Speed Leveling", "Let players/dinos level movement speed (ASA).", "bool", "False", 0, 1),
    ("game", _GM, "bAllowFlyerSpeedLeveling", "Allow Flyer Speed Leveling", "Let flyers level movement speed.", "bool", "False", 0, 1),
    ("game", _GM, "bUseDinoLevelUpAnimations", "Dino Level-Up Animation", "Play an animation on dino level-up.", "bool", "True", 0, 1),
    ("game", _GM, "DestroyTamesOverLevelClamp", "Destroy Tames Over Level", "Delete tames above this level on restart (0 = off).", "int", "0", 0, 1000, 10),
]

# Defaults per the official wiki – stats 0 (Health) and 8 (Damage) differ.
_DINO_STAT_DEFAULTS = {
    #            Wild  Tamed  Add    Affinity
    0:          (1.0,  0.2,   0.14,  0.44),   # Health
    8:          (1.0,  0.17,  0.14,  0.44),   # Damage
}

INI_SETTINGS_DINO_STATS: list = []
for _si, _sn in enumerate(_STAT_NAMES):
    _wd, _td, _ad, _fd = _DINO_STAT_DEFAULTS.get(_si, (1.0, 1.0, 1.0, 1.0))
    INI_SETTINGS_DINO_STATS.append(
        ("game", _GM, f"PerLevelStatsMultiplier_DinoWild[{_si}]",
         f"Wild {_sn}", f"Wild dino {_sn} gain per level.", "float",
         str(_wd), 0.0, 10.0))
    INI_SETTINGS_DINO_STATS.append(
        ("game", _GM, f"PerLevelStatsMultiplier_DinoTamed[{_si}]",
         f"Tamed {_sn}", f"Tamed dino {_sn} gain per level.", "float",
         str(_td), 0.0, 10.0))
    INI_SETTINGS_DINO_STATS.append(
        ("game", _GM, f"PerLevelStatsMultiplier_DinoTamed_Add[{_si}]",
         f"Tamed Add {_sn}",
         f"Flat {_sn} bonus applied once when a wild creature is first tamed "
         f"(additive, independent of taming effectiveness).", "float",
         str(_ad), 0.0, 10.0))
    INI_SETTINGS_DINO_STATS.append(
        ("game", _GM, f"PerLevelStatsMultiplier_DinoTamed_Affinity[{_si}]",
         f"Affinity {_sn}",
         f"{_sn} bonus that scales with Taming Effectiveness (TE) \u2014 "
         f"higher TE yields a larger bonus; this multiplier adjusts that bonus.", "float",
         str(_fd), 0.0, 10.0))

INI_SETTINGS_PLAYERS: list = [
    ("gus", _SS, "PlayerDamageMultiplier", "Player Damage", "Scales damage dealt by players.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "PlayerResistanceMultiplier", "Player Resistance", "Scales damage resistance (higher = more damage taken).", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "PlayerCharacterFoodDrainMultiplier", "Food Drain", "Scales food consumption rate.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "PlayerCharacterHealthRecoveryMultiplier", "Health Recovery", "Scales passive health regen.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "PlayerCharacterStaminaDrainMultiplier", "Stamina Drain", "Scales stamina consumption.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "PlayerCharacterWaterDrainMultiplier", "Water Drain", "Scales water consumption.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "XPMultiplier", "XP Multiplier", "Scales all experience gain.", "float", "1.0", 0.1, 100.0, 1.0),
    ("gus", _SS, "HarvestAmountMultiplier", "Harvest Amount", "Scales resources gained per hit.", "float", "1.0", 0.1, 100.0, 1.0),
    ("gus", _SS, "HarvestHealthMultiplier", "Harvest Health", "Scales health of harvestables (more hits = more yield).", "float", "1.0", 0.1, 20.0),
    ("gus", _SS, "OxygenSwimSpeedStatMultiplier", "Oxygen Swim Speed", "Scales swim speed gained from Oxygen stat.", "float", "1.0", 0.0, 10.0),
    ("game", _GM, "KillXPMultiplier", "Kill XP", "Scales XP from kills.", "float", "1.0", 0.1, 50.0),
    ("game", _GM, "HarvestXPMultiplier", "Harvest XP", "Scales XP from harvesting.", "float", "1.0", 0.1, 50.0),
    ("game", _GM, "CraftXPMultiplier", "Craft XP", "Scales XP from crafting.", "float", "1.0", 0.1, 50.0),
    ("game", _GM, "GenericXPMultiplier", "Generic XP", "Scales XP from passive/time gain.", "float", "1.0", 0.1, 50.0),
    ("game", _GM, "SpecialXPMultiplier", "Special XP", "Scales XP from special events.", "float", "1.0", 0.1, 50.0),
    ("game", _GM, "bAllowUnlimitedRespecs", "Unlimited Respecs", "Allow Mindwipe Tonic without cooldown.", "bool", "False", 0, 1),
]

INI_SETTINGS_PLAYER_STATS: list = []
for _si, _sn in enumerate(_STAT_NAMES):
    INI_SETTINGS_PLAYER_STATS.append(
        ("game", _GM, f"PerLevelStatsMultiplier_Player[{_si}]",
         f"Player {_sn}", f"Player {_sn} gain per level.", "float",
         "1.0", 0.0, 10.0))

INI_SETTINGS_MISC: list = [
    ("gus", _SS, "ItemStackSizeMultiplier", "Item Stack Size", "Scales default stack sizes.", "float", "1.0", 0.1, 50.0),
    ("gus", _SS, "ResourcesRespawnPeriodMultiplier", "Resource Respawn Period", "Scales resource respawn timer.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "StructureResistanceMultiplier", "Structure Resistance", "Scales structure damage resistance.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "StructurePickupHoldDuration", "Pickup Hold Duration", "Seconds to hold for quick-pickup (0 = instant).", "float", "0.5", 0.0, 5.0),
    ("gus", _SS, "StructurePickupTimeAfterPlacement", "Pickup Time After Place",
     "Seconds after placing a structure during which pickup is still allowed. "
     "After this window closes, the structure becomes permanent "
     "(unless Always Allow Pickup is on). 0 = no pickup window.", "float", "30.0", 0.0, 600.0, 5.0),
    ("gus", _SS, "TheMaxStructuresInRange", "Max Structures In Range", "Cap on structures in a coded radius.", "int", "10500", 100, 50000, 500),
    ("gus", _SS, "PerPlatformMaxStructuresMultiplier", "Platform Struct Multiplier", "Scales max items on saddles/rafts.", "float", "1.0", 0.1, 10.0),
    ("gus", _SS, "PlatformSaddleBuildAreaBoundsMultiplier", "Platform Build Area", "Scales platform saddle build range.", "float", "1.0", 0.1, 10.0),
    ("gus", _SS, "AlwaysAllowStructurePickup", "Always Allow Pickup", "Structures can always be picked up.", "bool", "False", 0, 1),
    ("gus", _SS, "ClampResourceHarvestDamage", "Clamp Harvest Damage", "Clamp harvest damage to resource health.", "bool", "False", 0, 1),
    ("gus", _SS, "ClampItemSpoilingTimes", "Clamp Item Spoiling", "Prevent spoil timers going below base.", "bool", "False", 0, 1),
    ("gus", _SS, "AllowMultipleAttachedC4", "Multiple C4 Attach", "Allow more than one C4 per creature.", "bool", "False", 0, 1),
    ("gus", _SS, "DisableDinoDecayPvE", "Disable Dino Decay (PvE)", "Prevent tame auto-decay in PvE.", "bool", "False", 0, 1),
    ("gus", _SS, "DisableStructureDecayPvE", "Disable Structure Decay (PvE)", "Prevent structure auto-decay in PvE.", "bool", "False", 0, 1),
    ("gus", _SS, "DisableCryopodEnemyCheck", "Cryopod No Enemy Check", "Use cryopods when enemies nearby (ASA).", "bool", "False", 0, 1),
    ("gus", _SS, "DisableCryopodFridgeRequirement", "Cryopod No Fridge", "Use cryopods without a cryofridge (ASA).", "bool", "False", 0, 1),
    ("gus", _SS, "AllowCryoFridgeOnSaddle", "Cryofridge On Saddle", "Allow cryofridge on platform saddles (ASA).", "bool", "False", 0, 1),
    ("gus", _SS, "MaxTrainCars", "Max Train Cars", "Max carts per train (ASA).", "int", "8", 1, 50),
    ("game", _GM, "HairGrowthSpeedMultiplier", "Hair Growth Speed", "Scales hair growth.", "float", "1.0", 0.0, 10.0),
    ("game", _GM, "PoopIntervalMultiplier", "Poop Interval", "Scales poop frequency (higher = less often).", "float", "1.0", 0.01, 10.0),
    ("game", _GM, "CustomRecipeEffectivenessMultiplier", "Custom Recipe Effectiveness", "Scales custom recipe results.", "float", "1.0", 0.1, 10.0),
    ("game", _GM, "CustomRecipeSkillMultiplier", "Custom Recipe Skill", "Scales crafting skill effect on recipes.", "float", "1.0", 0.1, 10.0),
    ("game", _GM, "ResourceNoReplenishRadiusPlayers", "No-Replenish Radius (Players)", "Distance from players resources won't regrow.", "float", "1.0", 0.0, 5.0),
    ("game", _GM, "ResourceNoReplenishRadiusStructures", "No-Replenish Radius (Structures)", "Distance from structures resources won't regrow.", "float", "1.0", 0.0, 5.0),
    ("game", _GM, "LimitGeneratorsNum", "Generator Limit (Count)", "Max generators in range (ASA).", "int", "3", 0, 50),
    ("game", _GM, "LimitGeneratorsRange", "Generator Limit (Range)", "Range in UE units for generator limit (ASA).", "int", "15000", 0, 100000, 1000),
    ("game", _GM, "BaseHexagonRewardMultiplier", "Hexagon Reward Multiplier", "Scales mission/club hex rewards.", "float", "1.0", 0.1, 50.0),
    ("game", _GM, "HexagonCostMultiplier", "Hexagon Cost Multiplier", "Scales hex store/club item costs.", "float", "1.0", 0.1, 50.0),
    ("game", _GM, "PhotoModeRangeLimit", "Photo Mode Range", "Max camera distance in photo mode (ASA).", "int", "3000", 0, 50000, 500),
    ("game", _GM, "bDisablePhotoMode", "Disable Photo Mode", "Completely disable photo mode (ASA).", "bool", "False", 0, 1),
    ("gus", _SS, "TribeNameChangeCooldown", "Tribe Rename Cooldown (min)", "Minutes between tribe name changes.", "float", "15.0", 0.0, 10080.0, 5.0),
    ("gus", _SS, "ImplantSuicideCD", "Implant Respawn Cooldown (s)", "Seconds between implant respawns (ASA).", "float", "28800.0", 0.0, 86400.0, 300.0),
    ("gus", _SS, "RCONServerGameLogBuffer", "RCON Log Buffer", "Lines kept in RCON game log buffer.", "int", "600", 0, 5000, 50),
]

# =============================================================================
# Reorganized Visual Tab Lists (ASA-focused)
# =============================================================================

INI_SETTINGS_ENVIRONMENT: list = INI_SETTINGS_GENERAL + INI_SETTINGS_CROPS + [
    # Resource & decay settings (moved from Misc)
    ("gus", _SS, "ResourcesRespawnPeriodMultiplier", "Resource Respawn Period", "Scales resource respawn timer.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "ClampResourceHarvestDamage", "Clamp Harvest Damage", "Clamp harvest damage to resource health.", "bool", "False", 0, 1),
    ("gus", _SS, "ClampItemSpoilingTimes", "Clamp Item Spoiling", "Prevent spoil timers going below base.", "bool", "False", 0, 1),
    ("gus", _SS, "DisableDinoDecayPvE", "Disable Dino Decay (PvE)", "Prevent tame auto-decay in PvE.", "bool", "False", 0, 1),
    ("game", _GM, "ResourceNoReplenishRadiusPlayers", "No-Replenish Radius (Players)", "Distance from players where resources won't regrow.", "float", "1.0", 0.0, 5.0),
    ("game", _GM, "ResourceNoReplenishRadiusStructures", "No-Replenish Radius (Structures)", "Distance from structures where resources won't regrow.", "float", "1.0", 0.0, 5.0),
    # Misc environment settings (moved from Misc)
    ("game", _GM, "HairGrowthSpeedMultiplier", "Hair Growth Speed", "Scales hair growth.", "float", "1.0", 0.0, 10.0),
    ("game", _GM, "PoopIntervalMultiplier", "Poop Interval", "Scales poop frequency (higher = less often).", "float", "1.0", 0.01, 10.0),
    ("game", _GM, "CustomRecipeEffectivenessMultiplier", "Custom Recipe Effectiveness", "Scales custom recipe results.", "float", "1.0", 0.1, 10.0),
    ("game", _GM, "CustomRecipeSkillMultiplier", "Custom Recipe Skill", "Scales crafting skill effect on recipes.", "float", "1.0", 0.1, 10.0),
    # New ASA environment settings
    ("gus", _SS, "PvEDinoDecayPeriodMultiplier", "PvE Dino Decay Period", "Scales creature auto-decay timer in PvE.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "PvPDinoDecay", "PvP Dino Decay", "Enable creature decay in PvP with ORP.", "bool", "False", 0, 1),
    ("gus", _SS, "PreventOfflinePvPInterval", "ORP Activation Delay (s)", "Seconds before ORP becomes active after logout.", "float", "0.0", 0.0, 3600.0, 10.0),
    ("gus", _SS, "RaidDinoCharacterFoodDrainMultiplier", "Raid Dino Food Drain", "Food drain speed for raid dinos (e.g. Titanosaur).", "float", "1.0", 0.01, 10.0),
]

# Dinos tab gets some extra cryopod settings from old Misc
INI_SETTINGS_DINOS_FULL: list = INI_SETTINGS_DINOS + [
    ("gus", _SS, "DisableCryopodEnemyCheck", "Cryopod No Enemy Check", "Use cryopods when enemies nearby (ASA).", "bool", "False", 0, 1),
    ("gus", _SS, "DisableCryopodFridgeRequirement", "Cryopod No Fridge", "Use cryopods without a cryofridge (ASA).", "bool", "False", 0, 1),
    ("gus", _SS, "AllowCryoFridgeOnSaddle", "Cryofridge On Saddle", "Allow cryofridge on platform saddles (ASA).", "bool", "False", 0, 1),
    ("gus", _SS, "DestroyTamesOverTheSoftTameLimit", "Destroy Over Soft Limit", "Auto-destroy tames over the soft tame limit (ASA).", "bool", "False", 0, 1),
    ("gus", _SS, "MaxTamedDinos_SoftTameLimit", "Soft Tame Limit", "Server-wide soft tame limit (ASA).", "int", "5000", 0, 20000, 100),
    ("gus", _SS, "MaxTamedDinos_SoftTameLimit_CountdownForDeletionDuration", "Soft Limit Deletion Countdown (s)", "Seconds before tames over soft limit are destroyed.", "int", "604800", 0, 2592000, 3600),
]

INI_SETTINGS_STRUCTURES: list = [
    ("gus", _SS, "StructureResistanceMultiplier", "Structure Resistance", "Scales structure damage resistance.", "float", "1.0", 0.01, 10.0),
    ("gus", _SS, "StructurePickupHoldDuration", "Pickup Hold Duration", "Seconds to hold for quick-pickup (0 = instant).", "float", "0.5", 0.0, 5.0),
    ("gus", _SS, "StructurePickupTimeAfterPlacement", "Pickup Time After Place",
     "Seconds after placing a structure during which pickup is still allowed.", "float", "30.0", 0.0, 600.0, 5.0),
    ("gus", _SS, "TheMaxStructuresInRange", "Max Structures In Range", "Cap on structures in a coded radius.", "int", "10500", 100, 50000, 500),
    ("gus", _SS, "PerPlatformMaxStructuresMultiplier", "Platform Struct Multiplier", "Scales max items on saddles/rafts.", "float", "1.0", 0.1, 10.0),
    ("gus", _SS, "PlatformSaddleBuildAreaBoundsMultiplier", "Platform Build Area", "Scales platform saddle build range.", "float", "1.0", 0.1, 10.0),
    ("gus", _SS, "AlwaysAllowStructurePickup", "Always Allow Pickup", "Structures can always be picked up.", "bool", "False", 0, 1),
    ("gus", _SS, "AllowMultipleAttachedC4", "Multiple C4 Attach", "Allow more than one C4 per creature.", "bool", "False", 0, 1),
    ("gus", _SS, "DisableStructureDecayPvE", "Disable Structure Decay (PvE)", "Prevent structure auto-decay in PvE.", "bool", "False", 0, 1),
    ("gus", _SS, "ForceAllStructureLocking", "Force Structure Locking", "Default-lock all placed structures.", "bool", "False", 0, 1),
    ("gus", _SS, "IgnoreLimitMaxStructuresInRangeTypeFlag", "Ignore Decorative Limit", "Remove the 150 decorative structure limit.", "bool", "False", 0, 1),
    ("gus", _SS, "OverrideStructurePlatformPrevention", "Override Platform Prevention", "Allow turrets/spikes on platform saddles.", "bool", "False", 0, 1),
    ("gus", _SS, "StructurePreventResourceRadiusMultiplier", "Struct Resource Block Radius", "Scales the no-resource-regrow radius around structures.", "float", "1.0", 0.0, 5.0),
    ("gus", _SS, "EnableExtraStructurePreventionVolumes", "Extra Prevention Volumes", "Block building near major resource areas.", "bool", "False", 0, 1),
    ("gus", _SS, "PvEAllowStructuresAtSupplyDrops", "PvE Build At Supply Drops", "Allow building near supply drops in PvE.", "bool", "False", 0, 1),
    ("game", _GM, "bDisableStructurePlacementCollision", "Disable Placement Collision", "Allow structures to clip into terrain.", "bool", "False", 0, 1),
    ("game", _GM, "bIgnoreStructuresPreventionVolumes", "Ignore Prevention Volumes", "Allow building in normally blocked areas.", "bool", "False", 0, 1),
    ("game", _GM, "LimitGeneratorsNum", "Generator Limit (Count)", "Max generators in range (ASA).", "int", "3", 0, 50),
    ("game", _GM, "LimitGeneratorsRange", "Generator Limit (Range)", "Range in UE units for generator limit (ASA).", "int", "15000", 0, 100000, 1000),
]

INI_SETTINGS_STACK_SIZE: list = [
    ("gus", _SS, "ItemStackSizeMultiplier", "Global Stack Size Multiplier",
     "Scales all default stack sizes (excludes items with stack size of 1).", "float", "1.0", 0.1, 100.0, 1.0),
]

INI_SETTINGS_SERVER_OPTIONS: list = [
    ("gus", _SS, "AdminLogging", "Admin Logging", "Log all admin commands to in-game chat.", "bool", "False", 0, 1),
    ("gus", _SS, "ServerHardcore", "Hardcore Mode", "Players revert to level 1 on death.", "bool", "False", 0, 1),
    ("gus", _SS, "ShowFloatingDamageText", "Floating Damage Text", "Show RPG-style popup damage numbers.", "bool", "False", 0, 1),
    ("gus", _SS, "DontAlwaysNotifyPlayerJoined", "Hide Join Notifications", "Disable player join notifications.", "bool", "False", 0, 1),
    ("gus", _SS, "PreventDiseases", "Prevent Diseases", "Completely disable diseases (e.g. Swamp Fever).", "bool", "False", 0, 1),
    ("gus", _SS, "AllowHideDamageSourceFromLogs", "Hide Damage Source In Logs", "Hide damage sources in tribe logs.", "bool", "True", 0, 1),
    ("gus", _SS, "KickIdlePlayersPeriod", "Idle Kick Period (s)", "Seconds before idle players are kicked (needs -EnableIdlePlayerKick).", "float", "3600.0", 0.0, 86400.0, 60.0),
    ("gus", _SS, "MaxTrainCars", "Max Train Cars", "Max carts per train (ASA).", "int", "8", 1, 50),
    ("gus", _SS, "TribeNameChangeCooldown", "Tribe Rename Cooldown (min)", "Minutes between tribe name changes.", "float", "15.0", 0.0, 10080.0, 5.0),
    ("gus", _SS, "ImplantSuicideCD", "Implant Respawn Cooldown (s)", "Seconds between implant respawns (ASA).", "float", "28800.0", 0.0, 86400.0, 300.0),
    ("gus", _SS, "RCONServerGameLogBuffer", "RCON Log Buffer", "Lines kept in RCON game log buffer.", "int", "600", 0, 5000, 50),
    ("game", _GM, "BaseHexagonRewardMultiplier", "Hexagon Reward Multiplier", "Scales mission/club hex rewards.", "float", "1.0", 0.1, 50.0),
    ("game", _GM, "HexagonCostMultiplier", "Hexagon Cost Multiplier", "Scales hex store/club item costs.", "float", "1.0", 0.1, 50.0),
    ("game", _GM, "PhotoModeRangeLimit", "Photo Mode Range", "Max camera distance in photo mode (ASA).", "int", "3000", 0, 50000, 500),
    ("game", _GM, "bDisablePhotoMode", "Disable Photo Mode", "Completely disable photo mode (ASA).", "bool", "False", 0, 1),
    ("game", _GM, "CraftingSkillBonusMultiplier", "Crafting Skill Bonus", "Scales the bonus from Crafting Skill stat.", "float", "1.0", 0.0, 10.0),
    ("game", _GM, "MaxFallSpeedMultiplier", "Max Fall Speed", "Scales falling speed threshold for fall damage.", "float", "1.0", 0.01, 100.0, 0.5),
]

# Spawn customization keys – complex multi-value entries edited via text areas.
# Each tuple: (ini_file, section, key, title, description)
_SPAWN_ENTRY_KEYS: list = [
    ("game", _GM, "NPCReplacements",
     "NPC Replacements",
     "Replace one dino species with another (or disable it with an empty ToClassName).\n"
     "Syntax: (FromClassName=\"Dino_Character_BP_C\",ToClassName=\"NewDino_Character_BP_C\")\n"
     "To disable a dino: (FromClassName=\"Dino_Character_BP_C\",ToClassName=\"\")"),
    ("game", _GM, "DinoSpawnWeightMultipliers",
     "Dino Spawn Weight Multipliers",
     "Adjust spawn rates and caps for specific dinos.\n"
     "Syntax: (DinoNameTag=<Tag>,SpawnWeightMultiplier=<X>,OverrideSpawnLimitPercentage=true,"
     "SpawnLimitPercentage=<Y>)"),
    ("game", _GM, "ConfigSubtractNPCSpawnEntriesContainer",
     "Remove NPC Spawn Entries",
     "Remove specific dinos from a spawn container.\n"
     "Syntax: (NPCSpawnEntriesContainerClassString=\"<ContainerID>\","
     "NPCSpawnEntries=((AnEntryName=\"Remove\",EntryWeight=1.0,"
     "NPCsToSpawnStrings=(\"Dino_Character_BP_C\"))))"),
    ("game", _GM, "ConfigAddNPCSpawnEntriesContainer",
     "Add NPC Spawn Entries",
     "Add custom dinos to an existing spawn container.\n"
     "Syntax: (NPCSpawnEntriesContainerClassString=\"<ContainerID>\","
     "NPCSpawnEntries=((AnEntryName=\"Add\",EntryWeight=1.0,"
     "NPCsToSpawnStrings=(\"Dino_Character_BP_C\"))))"),
    ("game", _GM, "ConfigOverrideNPCSpawnEntriesContainer",
     "Override NPC Spawn Entries",
     "Completely replace all spawn entries of a container.\n"
     "Syntax: (NPCSpawnEntriesContainerClassString=\"<ContainerID>\","
     "NPCSpawnEntries=((AnEntryName=\"Override\",EntryWeight=1.0,"
     "NPCsToSpawnStrings=(\"Dino_Character_BP_C\"))))"),
]
