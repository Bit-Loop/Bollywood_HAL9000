ALTER TABLE degradation_episodes
ADD COLUMN required_capabilities_json TEXT NOT NULL DEFAULT '[]';
