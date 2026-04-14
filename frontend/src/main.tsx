import React from 'react'
import ReactDOM from 'react-dom/client'
import { initSentry } from './lib/sentry'
import App from './App'
import './index.css'
import './store/theme' // initialize theme on load

initSentry()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
