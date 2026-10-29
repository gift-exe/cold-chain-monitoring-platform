-- Minimal reference data for development and the I1 tests. Names are real places; the facilities,
-- devices and codes below are invented for testing. Jurisdiction codes: ISO 3166 for nation and state,
-- project-internal three-letter suffixes for LGAs.

INSERT INTO jurisdiction (id, level, name, parent_id) VALUES
    ('NG',        'nation', 'Nigeria', NULL),
    ('NG-KN',     'state',  'Kano',    'NG'),
    ('NG-KN-GWL', 'lga',    'Gwale',   'NG-KN'),
    ('NG-KN-DAL', 'lga',    'Dala',    'NG-KN')
ON CONFLICT (id) DO NOTHING;

INSERT INTO facility (id, name, tier, jurisdiction_id, parent_facility_id, location, power_source) VALUES
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d10', 'Kano State Cold Store', 'state_store', 'NG-KN', NULL,
        'SRID=4326;POINT(8.5167 12.0000)', 'grid_generator'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d11', 'Gwale LGA Cold Store', 'lga_store', 'NG-KN-GWL',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d10', 'SRID=4326;POINT(8.4930 11.9870)', 'solar'),
    ('8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d01', 'Gwale Test Health Facility', 'health_facility', 'NG-KN-GWL',
        '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d11', 'SRID=4326;POINT(8.4880 11.9800)', 'grid')
ON CONFLICT (id) DO NOTHING;

INSERT INTO product (id, name, lower_limit, upper_limit, freeze_sensitive) VALUES
    ('5c1d0a00-0000-4000-8000-000000000001', 'Pentavalent (DTP-HepB-Hib)', 2.0, 8.0, true),
    ('5c1d0a00-0000-4000-8000-000000000002', 'Measles-Rubella', 2.0, 8.0, false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO device (id, serial_no, facility_id, type, equipment_type, has_door_sensor, has_power_signal) VALUES
    ('d0000000-0000-4000-8000-000000000001', 'TEST-FIX-0001', '8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d01',
        'fixed', 'ice_lined_refrigerator', true, true),
    ('d0000000-0000-4000-8000-000000000002', 'TEST-MOB-0001', NULL, 'mobile', 'vaccine_carrier', false, false)
ON CONFLICT (id) DO NOTHING;
