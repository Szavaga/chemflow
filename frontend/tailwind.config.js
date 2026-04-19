/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  corePlugins: {
    // Disable Tailwind's preflight reset so it doesn't clash with the
    // existing index.css base styles (auth page, navbar, legacy sim page).
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        // teal is already in Tailwind's default palette; no additions needed
      },
    },
  },
}
