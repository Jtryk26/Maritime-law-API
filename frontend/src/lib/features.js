/**
 * Funktionsflag, fastlagt ved build.
 *
 * VITE_GEOGRAPHY_ENABLED=true viser kortet over geografisk anvendelighed
 * (eksperimentel). Det er kun en visningsbeslutning: backend udstiller de
 * geografiske ruter alene med GEOGRAPHY_ENABLED=true og håndhæver det
 * uafhængigt af frontend.
 */
export const GEOGRAPHY_ENABLED = import.meta.env.VITE_GEOGRAPHY_ENABLED === 'true'
