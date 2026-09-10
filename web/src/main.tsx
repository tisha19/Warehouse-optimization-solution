import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { LiveProvider } from './state/LiveState'
import './styles/tokens.css'
import './styles/panels.css'
import './App.css'

createRoot(document.getElementById('root')!).render(
    <StrictMode>
        <BrowserRouter>
            <LiveProvider>
                <App />
            </LiveProvider>
        </BrowserRouter>
    </StrictMode>,
)
