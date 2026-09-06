// Format hints are priors, never transcript corrections or proof of readability.
export const stateCodes =
  'AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split(
    ' ',
  );
export const plateStyles = ['unknown', 'ut_skier', 'ut_arches', 'personalized', 'other'];
export function formatHint(jurisdiction, style, text) {
  if (jurisdiction !== 'UT' || ['personalized', 'other'].includes(style)) return null;
  const known = ['ut_skier', 'ut_arches'].includes(style);
  return {
    matches: /^[A-Z][0-9]{3}[A-Z]{2}$/.test(text.toUpperCase().replace(/[\s-]/g, '')),
    known,
    description: 'Utah Skier and Arches standard issues: one letter, three digits, two letters (A12 3BC).',
    source: 'https://dmv.utah.gov/plates/license-plates/' + (style === 'ut_arches' ? 'arches/' : 'skier/'),
  };
}
