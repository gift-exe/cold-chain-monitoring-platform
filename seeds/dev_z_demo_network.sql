-- Demo network for the dashboard (I6): three LGAs of Kano metropolis with their cold stores, health
-- facilities and outreach loggers. LGA names and approximate locations are real; every facility and
-- device here is invented, and named "Demo" to make that plain. Loaded after dev_reference.sql (name
-- order). Readings come from scripts/demo.py. The network calibrated from OpenStreetMap is iteration I7.

INSERT INTO jurisdiction (id, level, name, parent_id) VALUES
    ('NG-KN-NAS', 'lga', 'Nassarawa', 'NG-KN')
ON CONFLICT (id) DO NOTHING;

INSERT INTO facility (id, name, tier, jurisdiction_id, parent_facility_id, location, power_source) VALUES
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e01', 'Dala LGA Cold Store', 'lga_store', 'NG-KN-DAL',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d10', 'SRID=4326;POINT(8.5010 12.0090)', 'solar'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e02', 'Nassarawa LGA Cold Store', 'lga_store', 'NG-KN-NAS',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d10', 'SRID=4326;POINT(8.5480 11.9840)', 'grid_generator'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e11', 'Gwale Demo PHC 1', 'health_facility', 'NG-KN-GWL',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d11', 'SRID=4326;POINT(8.4790 11.9930)', 'solar'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e12', 'Gwale Demo PHC 2', 'health_facility', 'NG-KN-GWL',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d11', 'SRID=4326;POINT(8.4990 11.9690)', 'grid'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e13', 'Gwale Demo PHC 3', 'health_facility', 'NG-KN-GWL',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d11', 'SRID=4326;POINT(8.4700 11.9760)', 'solar'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e21', 'Dala Demo PHC 1', 'health_facility', 'NG-KN-DAL',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e01', 'SRID=4326;POINT(8.4930 12.0210)', 'grid'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e22', 'Dala Demo PHC 2', 'health_facility', 'NG-KN-DAL',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e01', 'SRID=4326;POINT(8.5120 12.0180)', 'solar'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e23', 'Dala Demo PHC 3', 'health_facility', 'NG-KN-DAL',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e01', 'SRID=4326;POINT(8.4850 12.0060)', 'grid_generator'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e31', 'Nassarawa Demo PHC 1', 'health_facility', 'NG-KN-NAS',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e02', 'SRID=4326;POINT(8.5390 11.9950)', 'grid'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e32', 'Nassarawa Demo PHC 2', 'health_facility', 'NG-KN-NAS',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e02', 'SRID=4326;POINT(8.5620 11.9720)', 'solar'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e33', 'Nassarawa Demo PHC 3', 'health_facility', 'NG-KN-NAS',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e02', 'SRID=4326;POINT(8.5550 11.9930)', 'grid')
ON CONFLICT (id) DO NOTHING;

-- One fixed logger per store and facility (the state store's and Gwale store's too); two outreach loggers kept at the Gwale LGA store
INSERT INTO device (serial_no, facility_id, type, equipment_type, has_door_sensor, has_power_signal)
SELECT 'DEMO-FIX-' || upper(right(f.id::text, 3)), f.id, 'fixed',
       CASE WHEN f.tier IN ('lga_store', 'state_store') THEN 'walk_in_cold_room' WHEN f.power_source = 'solar' THEN 'solar_direct_drive'
            ELSE 'ice_lined_refrigerator' END,
       true, f.power_source <> 'solar'
FROM facility f WHERE f.id::text LIKE '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e%'
   OR f.id IN ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d10', '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d11')
ON CONFLICT (serial_no) DO NOTHING;

INSERT INTO device (serial_no, facility_id, type, equipment_type, has_door_sensor, has_power_signal) VALUES
    ('DEMO-MOB-0001', NULL, 'mobile', 'vaccine_carrier', false, false),
    ('DEMO-MOB-0002', NULL, 'mobile', 'cold_box', false, false)
ON CONFLICT (serial_no) DO NOTHING;

-- Wards (E9, P07): the real GRID3 ward of the nearest GRID3 facility in the same LGA (state store: nearest
-- in Kano metropolis). The facilities are invented; the wards are real.
UPDATE facility f SET ward = w.ward FROM (VALUES
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d10', 'Chedi'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d11', 'Sani Mainagge'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d01', 'Sani Mainagge'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e01', 'Yalwa Dala'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e02', 'Giginyu'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e11', 'Kabuga'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e12', 'Dorayi'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e13', 'Dorayi'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e21', 'Kofar Ruwa'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e22', 'Gwammaja'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e23', 'Yalwa Dala'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e31', 'Giginyu'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e32', 'Hotoro South'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e33', 'Giginyu')
) AS w (id, ward) WHERE f.id = w.id::uuid;
