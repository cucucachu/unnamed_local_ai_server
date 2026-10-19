/**
 * Unit conversions. Each unit is a factor to the group's base unit (metres,
 * kilograms, litres, m/s, m²); temperature converts through Celsius.
 */
export type Unit = { key: string; label: string; factor?: number; toBase?: (v: number) => number; fromBase?: (v: number) => number };
export type Group = { key: string; label: string; units: Unit[] };

const US_CUP = 0.2365882365;

export const GROUPS: Group[] = [
  {
    key: 'length',
    label: 'Length',
    units: [
      { key: 'mm', label: 'millimetres (mm)', factor: 0.001 },
      { key: 'cm', label: 'centimetres (cm)', factor: 0.01 },
      { key: 'm', label: 'metres (m)', factor: 1 },
      { key: 'km', label: 'kilometres (km)', factor: 1000 },
      { key: 'in', label: 'inches (in)', factor: 0.0254 },
      { key: 'ft', label: 'feet (ft)', factor: 0.3048 },
      { key: 'yd', label: 'yards (yd)', factor: 0.9144 },
      { key: 'mi', label: 'miles (mi)', factor: 1609.344 },
    ],
  },
  {
    key: 'mass',
    label: 'Weight',
    units: [
      { key: 'g', label: 'grams (g)', factor: 0.001 },
      { key: 'kg', label: 'kilograms (kg)', factor: 1 },
      { key: 'oz', label: 'ounces (oz)', factor: 0.028349523125 },
      { key: 'lb', label: 'pounds (lb)', factor: 0.45359237 },
      { key: 'st', label: 'stone (st)', factor: 6.35029318 },
    ],
  },
  {
    key: 'volume',
    label: 'Kitchen',
    units: [
      { key: 'ml', label: 'millilitres (ml)', factor: 0.001 },
      { key: 'l', label: 'litres (l)', factor: 1 },
      { key: 'tsp', label: 'teaspoons (tsp)', factor: US_CUP / 48 },
      { key: 'tbsp', label: 'tablespoons (tbsp)', factor: US_CUP / 16 },
      { key: 'floz', label: 'US fluid ounces (fl oz)', factor: US_CUP / 8 },
      { key: 'cup', label: 'US cups', factor: US_CUP },
      { key: 'pt', label: 'US pints (pt)', factor: US_CUP * 2 },
      { key: 'gal', label: 'US gallons (gal)', factor: US_CUP * 16 },
    ],
  },
  {
    key: 'temperature',
    label: 'Temperature',
    units: [
      { key: 'c', label: 'Celsius (°C)', toBase: (v) => v, fromBase: (v) => v },
      { key: 'f', label: 'Fahrenheit (°F)', toBase: (v) => ((v - 32) * 5) / 9, fromBase: (v) => (v * 9) / 5 + 32 },
      { key: 'k', label: 'Kelvin (K)', toBase: (v) => v - 273.15, fromBase: (v) => v + 273.15 },
    ],
  },
  {
    key: 'speed',
    label: 'Speed',
    units: [
      { key: 'kmh', label: 'km per hour', factor: 1000 / 3600 },
      { key: 'mph', label: 'miles per hour', factor: 1609.344 / 3600 },
      { key: 'ms', label: 'metres per second', factor: 1 },
      { key: 'kn', label: 'knots', factor: 1852 / 3600 },
    ],
  },
  {
    key: 'area',
    label: 'Area',
    units: [
      { key: 'm2', label: 'square metres (m²)', factor: 1 },
      { key: 'ft2', label: 'square feet (ft²)', factor: 0.09290304 },
      { key: 'acre', label: 'acres', factor: 4046.8564224 },
      { key: 'ha', label: 'hectares (ha)', factor: 10000 },
    ],
  },
];

export function convert(value: number, from: Unit, to: Unit): number {
  const base = from.toBase ? from.toBase(value) : value * from.factor!;
  return to.fromBase ? to.fromBase(base) : base / to.factor!;
}
