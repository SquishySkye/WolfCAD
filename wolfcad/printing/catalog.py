# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Die einstellbaren Werte des Druck-Reiters: deutsche Bezeichnung, Gruppe und Art je Profilschlüssel.

Aufteilung wie in Creality Print: Prozess mit den Reitern Qualität, Festigkeit, Geschwindigkeit, Stützen und
Sonstiges; Filament und Drucker mit ihren wichtigsten Gruppen. Die Schlüssel sind die der Profildateien (OrcaSlicer-
Format). Werte, die hier nicht stehen, bleiben wie im Profil; über „Alle Werte“ lassen sie sich trotzdem ändern.

Eintrag: (Schlüssel, Bezeichnung, Einheit, Auswahl) – Auswahl ist ein Wörterbuch Profilwert -> Anzeige für
Aufzählungen, None für Zahlen/Text; Ja/Nein-Werte („0“/„1“) erkennt der Reiter selbst.
"""
from __future__ import annotations

PATTERNS = {"monotonic": "Monoton", "monotonicline": "Monotone Linie", "rectilinear": "Geradlinig",
            "alignedrectilinear": "Ausgerichtet geradlinig", "zig-zag": "Zickzack", "crosszag": "Kreuz-Zickzack",
            "grid": "Gitter", "line": "Linie", "cubic": "Kubisch", "triangles": "Dreiecke",
            "tri-hexagon": "Tri-Hexagon", "gyroid": "Gyroid", "honeycomb": "Waben", "adaptivecubic": "Adaptiv kubisch",
            "alignedrectilinear ": "Ausgerichtet", "3dhoneycomb": "3D-Waben", "hilbertcurve": "Hilbert-Kurve",
            "archimedeanchords": "Archimedische Sehnen", "octagramspiral": "Achtstern-Spirale",
            "supportcubic": "Stützkubisch", "lightning": "Blitz", "concentric": "Konzentrisch",
            "crosshatch": "Kreuzschraffur", "quartercubic": "Viertelkubisch"}
SURFACE_PATTERNS = {key: PATTERNS[key] for key in ("monotonic", "monotonicline", "rectilinear",
                                                   "alignedrectilinear", "concentric", "hilbertcurve",
                                                   "archimedeanchords", "octagramspiral")}

PROCESS = {
    "Qualität": [
        ("layer_height", "Schichthöhe", "mm", None),
        ("initial_layer_print_height", "Höhe der ersten Schicht", "mm", None),
        ("line_width", "Standard-Linienbreite", "mm", None),
        ("initial_layer_line_width", "Linienbreite erste Schicht", "mm", None),
        ("outer_wall_line_width", "Linienbreite Außenwand", "mm", None),
        ("inner_wall_line_width", "Linienbreite Innenwand", "mm", None),
        ("top_surface_line_width", "Linienbreite Oberfläche", "mm", None),
        ("sparse_infill_line_width", "Linienbreite Füllung", "mm", None),
        ("print_flow_ratio", "Flussverhältnis", "", None),
        ("seam_position", "Nahtposition", "", {"nearest": "Nächste", "aligned": "Ausgerichtet",
                                              "back": "Hinten", "random": "Zufällig"}),
        ("seam_gap", "Nahtlücke", "", None),
        ("precise_outer_wall", "Präzise Außenwand", "", None),
        ("xy_contour_compensation", "XY-Konturausgleich", "mm", None),
        ("xy_hole_compensation", "XY-Lochausgleich", "mm", None),
        ("elefant_foot_compensation", "Elefantenfuß-Ausgleich", "mm", None),
        ("ironing_type", "Bügeln", "", {"no ironing": "Kein Bügeln", "top": "Oberste Fläche",
                                        "topmost": "Nur oberste Schicht", "solid": "Alle massiven Schichten"}),
        ("ironing_flow", "Bügel-Fluss", "%", None),
        ("ironing_spacing", "Bügel-Linienabstand", "mm", None),
        ("ironing_speed", "Bügel-Geschwindigkeit", "mm/s", None),
        ("wall_generator", "Wandgenerator", "", {"classic": "Klassisch", "arachne": "Arachne"}),
        ("detect_thin_wall", "Dünne Wände erkennen", "", None),
        ("detect_overhang_wall", "Überhangwände erkennen", "", None),
        ("make_overhang_printable", "Überhänge druckbar machen", "", None),
        ("resolution", "Auflösung", "mm", None),
        ("enable_arc_fitting", "Bögen als G2/G3", "", None),
    ],
    "Festigkeit": [
        ("wall_loops", "Wandlinien", "", None),
        ("alternate_extra_wall", "Abwechselnd zusätzliche Wand", "", None),
        ("top_shell_layers", "Obere Schichten", "", None),
        ("top_shell_thickness", "Mindestdicke oben", "mm", None),
        ("bottom_shell_layers", "Untere Schichten", "", None),
        ("bottom_shell_thickness", "Mindestdicke unten", "mm", None),
        ("top_surface_pattern", "Muster Oberfläche", "", SURFACE_PATTERNS),
        ("bottom_surface_pattern", "Muster Unterseite", "", SURFACE_PATTERNS),
        ("internal_solid_infill_pattern", "Muster massive Füllung", "", SURFACE_PATTERNS),
        ("sparse_infill_density", "Fülldichte", "%", None),
        ("sparse_infill_pattern", "Füllmuster", "", PATTERNS),
        ("infill_direction", "Füllwinkel", "°", None),
        ("infill_wall_overlap", "Überlappung Füllung/Wand", "%", None),
        ("infill_combination", "Füllung zusammenfassen", "", None),
        ("minimum_sparse_infill_area", "Mindestfläche für Füllung", "mm²", None),
        ("ensure_vertical_shell_thickness", "Senkrechte Wanddicke sicherstellen", "", None),
        ("wall_sequence", "Wandreihenfolge", "", {"inner wall/outer wall": "Innen/Außen",
                                                  "outer wall/inner wall": "Außen/Innen",
                                                  "inner-outer-inner wall": "Innen/Außen/Innen"}),
        ("is_infill_first", "Füllung vor Wänden", "", None),
    ],
    "Geschwindigkeit": [
        ("initial_layer_speed", "Erste Schicht", "mm/s", None),
        ("initial_layer_infill_speed", "Füllung erste Schicht", "mm/s", None),
        ("outer_wall_speed", "Außenwand", "mm/s", None),
        ("inner_wall_speed", "Innenwand", "mm/s", None),
        ("small_perimeter_speed", "Kleine Umfänge", "", None),
        ("sparse_infill_speed", "Füllung", "mm/s", None),
        ("internal_solid_infill_speed", "Massive Füllung", "mm/s", None),
        ("top_surface_speed", "Oberfläche", "mm/s", None),
        ("gap_infill_speed", "Lückenfüllung", "mm/s", None),
        ("support_speed", "Stützen", "mm/s", None),
        ("support_interface_speed", "Stützenschnittstelle", "mm/s", None),
        ("bridge_speed", "Brücken", "mm/s", None),
        ("travel_speed", "Fahrten", "mm/s", None),
        ("enable_overhang_speed", "Langsamer bei Überhängen", "", None),
        ("default_acceleration", "Standard-Beschleunigung", "mm/s²", None),
        ("outer_wall_acceleration", "Beschleunigung Außenwand", "mm/s²", None),
        ("inner_wall_acceleration", "Beschleunigung Innenwand", "mm/s²", None),
        ("top_surface_acceleration", "Beschleunigung Oberfläche", "mm/s²", None),
        ("sparse_infill_acceleration", "Beschleunigung Füllung", "mm/s²", None),
        ("initial_layer_acceleration", "Beschleunigung erste Schicht", "mm/s²", None),
        ("travel_acceleration", "Beschleunigung Fahrten", "mm/s²", None),
    ],
    "Stützen": [
        ("enable_support", "Stützen erzeugen", "", None),
        ("support_type", "Art", "", {"normal(auto)": "Normal (automatisch)", "tree(auto)": "Baum (automatisch)",
                                     "normal(manual)": "Normal (manuell)", "tree(manual)": "Baum (manuell)"}),
        ("support_style", "Stil", "", {"default": "Standard", "grid": "Gitter", "snug": "Eng anliegend",
                                       "tree_slim": "Baum schlank", "tree_strong": "Baum kräftig",
                                       "tree_hybrid": "Baum hybrid", "tree_organic": "Organisch"}),
        ("support_threshold_angle", "Überhangwinkel", "°", None),
        ("support_on_build_plate_only", "Nur auf dem Druckbett", "", None),
        ("support_critical_regions_only", "Nur kritische Bereiche", "", None),
        ("support_remove_small_overhang", "Kleine Überhänge ignorieren", "", None),
        ("support_top_z_distance", "Abstand oben (Z)", "mm", None),
        ("support_bottom_z_distance", "Abstand unten (Z)", "mm", None),
        ("support_object_xy_distance", "Abstand zum Modell (XY)", "mm", None),
        ("support_base_pattern", "Grundmuster", "", {"default": "Standard", "rectilinear": "Geradlinig",
                                                    "rectilinear-grid": "Gitter", "honeycomb": "Waben",
                                                    "lightning": "Blitz", "hollow": "Hohl"}),
        ("support_base_pattern_spacing", "Musterabstand", "mm", None),
        ("support_interface_top_layers", "Schnittstellenschichten oben", "", None),
        ("support_interface_bottom_layers", "Schnittstellenschichten unten", "", None),
        ("support_interface_pattern", "Schnittstellenmuster", "", {"auto": "Automatisch",
                                                                   "rectilinear": "Geradlinig",
                                                                   "concentric": "Konzentrisch",
                                                                   "rectilinear_interlaced": "Verschränkt",
                                                                   "grid": "Gitter"}),
        ("support_interface_spacing", "Schnittstellen-Linienabstand", "mm", None),
        ("bridge_no_support", "Keine Stützen unter Brücken", "", None),
        ("tree_support_branch_angle", "Baum: Astwinkel", "°", None),
        ("tree_support_branch_diameter", "Baum: Astdurchmesser", "mm", None),
        ("tree_support_wall_count", "Baum: Wandlinien", "", None),
        ("raft_layers", "Floß-Schichten", "", None),
    ],
    "Sonstiges": [
        ("curr_bed_type", "Druckplatte", "", {"Textured PEI Plate": "Strukturiertes PEI",
                                             "Hot Plate": "Glattes PEI", "Cool Plate": "Kalte Platte",
                                             "Engineering Plate": "Technische Platte",
                                             "Epoxy Resin Plate": "Epoxidplatte"}),
        ("brim_type", "Randart", "", {"auto_brim": "Automatisch", "brim_ears": "Mausohren",
                                      "outer_only": "Nur außen", "inner_only": "Nur innen",
                                      "outer_and_inner": "Außen und innen", "no_brim": "Kein Rand"}),
        ("brim_width", "Randbreite", "mm", None),
        ("brim_object_gap", "Abstand Rand/Modell", "mm", None),
        ("skirt_loops", "Saum-Linien", "", None),
        ("skirt_distance", "Saum-Abstand", "mm", None),
        ("skirt_height", "Saum-Höhe (Schichten)", "", None),
        ("print_sequence", "Druckreihenfolge", "", {"by layer": "Schichtweise", "by object": "Objektweise"}),
        ("spiral_mode", "Spiralvase", "", None),
        ("fuzzy_skin", "Raue Oberfläche", "", {"none": "Aus", "external": "Außen", "all": "Alle Wände",
                                               "allwalls": "Alle Wände (innen und außen)"}),
        ("fuzzy_skin_thickness", "Raue Oberfläche: Stärke", "mm", None),
        ("fuzzy_skin_point_distance", "Raue Oberfläche: Punktabstand", "mm", None),
        ("timelapse_type", "Zeitraffer", "", {"0": "Herkömmlich", "1": "Glatt"}),
        ("enable_prime_tower", "Reinigungsturm", "", None),
        ("prime_tower_width", "Reinigungsturm: Breite", "mm", None),
        ("prime_volume", "Reinigungsturm: Spülmenge", "mm³", None),
        ("flush_into_infill", "In Füllung spülen", "", None),
        ("flush_into_support", "In Stützen spülen", "", None),
        ("exclude_object", "Objekte einzeln abbrechbar", "", None),
        ("gcode_label_objects", "Objekte im G-Code kennzeichnen", "", None),
        ("reduce_crossing_wall", "Wände nicht überqueren", "", None),
        ("max_travel_detour_distance", "Größter Umweg", "mm", None),
        ("slicing_mode", "Schneidmodus", "", {"regular": "Normal", "even_odd": "Gerade/Ungerade",
                                             "close_holes": "Löcher schließen"}),
    ],
}

FILAMENT = {
    "Filament": [
        ("filament_type", "Material", "", None),
        ("filament_diameter", "Durchmesser", "mm", None),
        ("filament_density", "Dichte", "g/cm³", None),
        ("filament_cost", "Preis", "€/kg", None),
        ("filament_flow_ratio", "Flussverhältnis", "", None),
        ("filament_max_volumetric_speed", "Max. Volumenstrom", "mm³/s", None),
        ("pressure_advance", "Druckvorlauf (Pressure Advance)", "", None),
    ],
    "Temperaturen": [
        ("nozzle_temperature_initial_layer", "Düse erste Schicht", "°C", None),
        ("nozzle_temperature", "Düse", "°C", None),
        ("hot_plate_temp_initial_layer", "Glattes PEI erste Schicht", "°C", None),
        ("hot_plate_temp", "Glattes PEI", "°C", None),
        ("textured_plate_temp_initial_layer", "Strukturiertes PEI erste Schicht", "°C", None),
        ("textured_plate_temp", "Strukturiertes PEI", "°C", None),
        ("cool_plate_temp", "Kalte Platte", "°C", None),
        ("chamber_temperature", "Kammer", "°C", None),
        ("activate_chamber_temp_control", "Kammertemperatur regeln", "", None),
    ],
    "Kühlung": [
        ("close_fan_the_first_x_layers", "Lüfter aus für erste Schichten", "", None),
        ("fan_min_speed", "Lüfter min.", "%", None),
        ("fan_max_speed", "Lüfter max.", "%", None),
        ("fan_cooling_layer_time", "Volle Kühlung unter Schichtzeit", "s", None),
        ("slow_down_for_layer_cooling", "Langsamer für Schichtkühlung", "", None),
        ("overhang_fan_speed", "Lüfter bei Überhängen", "%", None),
        ("additional_cooling_fan_speed", "Zusatzlüfter", "%", None),
        ("during_print_exhaust_fan_speed", "Abluft beim Druck", "%", None),
    ],
    "Rückzug": [
        ("filament_retraction_length", "Rückzugslänge", "mm", None),
        ("filament_retraction_speed", "Rückzugsgeschwindigkeit", "mm/s", None),
        ("filament_z_hop", "Z-Hub", "mm", None),
    ],
}

MACHINE = {
    "Drucker": [
        ("printable_area", "Druckbereich", "mm", None),
        ("printable_height", "Bauhöhe", "mm", None),
        ("nozzle_diameter", "Düsendurchmesser", "mm", None),
        ("retraction_length", "Rückzugslänge", "mm", None),
        ("retraction_speed", "Rückzugsgeschwindigkeit", "mm/s", None),
        ("z_hop", "Z-Hub", "mm", None),
        ("machine_max_acceleration_x", "Max. Beschleunigung X", "mm/s²", None),
        ("machine_max_acceleration_y", "Max. Beschleunigung Y", "mm/s²", None),
        ("machine_max_speed_x", "Max. Geschwindigkeit X", "mm/s", None),
        ("machine_max_speed_y", "Max. Geschwindigkeit Y", "mm/s", None),
    ],
}

# Werte, die nicht in jedem Profil stehen, mit der Vorgabe des Originals
DEFAULTS = {"curr_bed_type": "Textured PEI Plate"}

# Schnellwahl oben im Prozessbereich wie in Creality Print („Einfach“-Ansicht)
QUICK = ["layer_height", "wall_loops", "sparse_infill_density", "sparse_infill_pattern", "enable_support",
         "support_type", "brim_type"]

CATALOGS = {"process": PROCESS, "filament": FILAMENT, "machine": MACHINE}


def entry(kind: str, key: str):
    """Der Katalogeintrag zu einem Schlüssel (oder None)."""
    for items in CATALOGS[kind].values():
        for item in items:
            if item[0] == key:
                return item
    return None


def is_flag(value) -> bool:
    """Ja/Nein-Wert im Profil („0“/„1“, auch als Liste mit einem Eintrag)?"""
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    return value in ("0", "1")


def scalar(value) -> str:
    """Anzeigewert: Listen mit einem Eintrag (Filamentwerte je Düse) als einzelner Wert."""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return "" if value is None else str(value)


def like(original, text: str):
    """Einen eingegebenen Text in der Form des Profilwerts ablegen (Liste bleibt Liste)."""
    if isinstance(original, list):
        parts = [part.strip() for part in text.split(",")] if "," in text and len(original) > 1 else [text.strip()]
        return parts if len(parts) == len(original) else [text.strip()] * max(1, len(original))
    return text.strip()
