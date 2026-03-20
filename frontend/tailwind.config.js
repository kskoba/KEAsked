/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './index.html',
    './src/**/*.{js,jsx}',
    './electron/**/*.{js,jsx}'
  ],
  theme: {
    extend: {
      colors: {
        'slate-900': '#0f172a',
        'slate-800': '#1e293b',
        'slate-700': '#334155',
        'slate-600': '#475569',
        'group-a': '#dbeafe',
        'group-b': '#dcfce7',
        'unfilled': '#fecaca'
      },
      minWidth: {
        'cell': '120px'
      },
      height: {
        'cell': '48px'
      }
    }
  },
  plugins: []
}
