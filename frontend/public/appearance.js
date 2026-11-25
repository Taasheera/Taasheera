// Apply the saved appearance before the app renders.
try {
  const appearance = localStorage.getItem('taasheera-appearance')
  if (appearance === 'light' || appearance === 'dark') {
    document.documentElement.dataset.appearance = appearance
  }
} catch {
  // The system setting still works when browser storage is unavailable.
}
