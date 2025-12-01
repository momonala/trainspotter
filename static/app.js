'use strict';

// =============================================================================
// Constants
// =============================================================================

const CONFIG = {
    FETCH_TIMEOUT_MS: 10000,
    RETRY_DELAY_MS: 5000,
    REFRESH_INTERVAL_MS: 30000,
    LAST_UPDATED_INTERVAL_MS: 1000,
    SCROLL_DEBOUNCE_MS: 50,
    WALK_TIME_BUFFER_MIN: 4,
    STATION_NAME_MAX_LENGTH: 20,
};

const TRANSPORT_LOGOS = {
    'S-Bahn': 'https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/S-Bahn-Logo.svg/2048px-S-Bahn-Logo.svg.png',
    'U-Bahn': 'https://upload.wikimedia.org/wikipedia/commons/e/ee/U-Bahn_Berlin_logo.svg',
    'Tram': 'https://upload.wikimedia.org/wikipedia/commons/a/a6/Tram-Logo.svg',
    'Bus': 'https://upload.wikimedia.org/wikipedia/commons/thumb/8/83/BUS-Logo-BVG.svg/1024px-BUS-Logo-BVG.svg.png',
    'DB': 'https://upload.wikimedia.org/wikipedia/commons/d/d5/Deutsche_Bahn_AG-Logo.svg',
};

// =============================================================================
// State
// =============================================================================

const state = {
    config: null,
    lastData: null,
    lastUpdatedAt: null,
    filters: {
        transport: 'all',
        direction: 'all',
        walkFilter: 'all'
    }
};

// =============================================================================
// Caches
// =============================================================================

const transportLogoCache = {};
const lineBadgeCache = {};

// =============================================================================
// API Functions
// =============================================================================

async function fetchStations(refresh = false) {
    try {
        const url = refresh ? '/api/stations?refresh=true' : '/api/stations';
        const resp = await fetch(url, {
            signal: AbortSignal.timeout(CONFIG.FETCH_TIMEOUT_MS)
        });
        if (!resp.ok) {
            throw new Error(`HTTP error! status: ${resp.status}`);
        }
        const data = await resp.json();
        state.config = data.config;
        return data;
    } catch (error) {
        console.error('Error fetching stations:', error);
        throw error;
    }
}

async function updateLocation() {
    if (!('geolocation' in navigator)) return false;
    
    try {
        const position = await new Promise((resolve, reject) => {
            navigator.geolocation.getCurrentPosition(resolve, reject);
        });
        
        await fetch('/api/location', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                latitude: position.coords.latitude,
                longitude: position.coords.longitude
            })
        });
        return true;
    } catch (error) {
        console.error('Error getting location:', error);
        return false;
    }
}

// =============================================================================
// Formatting Helpers
// =============================================================================

function formatDistance(meters) {
    if (meters < 1000) {
        return `${meters}m`;
    }
    return `${(meters / 1000).toFixed(1)}km`;
}

// =============================================================================
// Time Formatting
// =============================================================================

function formatRelativeAgo(fromDate) {
    if (!fromDate) return '';
    const now = new Date();
    let totalSeconds = Math.floor((now - fromDate) / 1000);
    if (totalSeconds < 0) totalSeconds = 0;
    
    const days = Math.floor(totalSeconds / 86400);
    totalSeconds %= 86400;
    const hours = Math.floor(totalSeconds / 3600);
    totalSeconds %= 3600;
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    
    const pad = (n) => n.toString().padStart(2, '0');
    
    if (days > 0) return `${days}d ${hours}:${pad(minutes)}:${pad(seconds)} ago`;
    if (hours > 0) return `${hours}:${pad(minutes)}:${pad(seconds)} ago`;
    if (minutes > 0) return `${minutes}:${pad(seconds)} ago`;
    return `${seconds}s ago`;
}

function renderLastUpdated() {
    const offsetEl = document.getElementById('last-updated-offset');
    const timeEl = document.getElementById('last-updated-time');
    
    if (timeEl) {
        timeEl.textContent = state.lastUpdatedAt 
            ? state.lastUpdatedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
            : '';
    }
    if (offsetEl) {
        offsetEl.textContent = formatRelativeAgo(state.lastUpdatedAt);
    }
}

// =============================================================================
// UI Helpers
// =============================================================================

function triggerRowAnimation() {
    // Add animation class to all table rows for split-flap flip effect
    const rows = document.querySelectorAll('.platform-table tbody tr');
    rows.forEach(row => {
        // Remove class first to reset animation if already present
        row.classList.remove('row-updated');
        // Force reflow to restart animation
        void row.offsetWidth;
        row.classList.add('row-updated');
    });
    
    // Remove animation class after all flips complete (to allow re-triggering)
    // Max delay is 750ms + 400ms animation = ~1150ms
    setTimeout(() => {
        rows.forEach(row => row.classList.remove('row-updated'));
    }, 1500);
}

function getTimeClass(minutesUntil, timeConfig) {
    if (!timeConfig || timeConfig.buffer === null) return '';
    
    if (minutesUntil < timeConfig.buffer) {
        return 'time-red';
    } else if (minutesUntil <= timeConfig.yellowThreshold) {
        return 'time-yellow';
    }
    return 'time-green';
}

function createLineBadge(line) {
    // Use cached badge if available
    const cacheKey = line.replace(/[^A-Z0-9]/g, '');
    if (lineBadgeCache[cacheKey]) {
        return lineBadgeCache[cacheKey].cloneNode(true);
    }

    const badge = document.createElement('span');
    badge.className = `line-badge line-${cacheKey}`;
    badge.textContent = line;
    lineBadgeCache[cacheKey] = badge;
    return badge.cloneNode(true);
}

function createTransportLogo(type) {
    if (transportLogoCache[type]) {
        return transportLogoCache[type].cloneNode(true);
    }

    const img = document.createElement('img');
    img.className = 'transport-logo';
    img.src = TRANSPORT_LOGOS[type] || '';
    img.alt = type;
    
    transportLogoCache[type] = img;
    return img.cloneNode(true);
}

function filterDeparture(departure, walkTime) {
    const { transport, direction, walkFilter } = state.filters;
    
    if (transport !== 'all' && departure.transport_type !== transport) {
        return false;
    }
    
    if (direction !== 'all' && departure.direction_symbol !== direction) {
        return false;
    }

    if (walkFilter === 'walkable' && walkTime != null) {
        const minutesUntil = Math.floor((new Date(departure.when) - new Date()) / 60000);
        const minTimeNeeded = walkTime - CONFIG.WALK_TIME_BUFFER_MIN;
        return minutesUntil > minTimeNeeded;
    }
    
    return true;
}

// =============================================================================
// Table Rendering
// =============================================================================

function createStationTable(departures, timeConfig, walkTime) {
    const table = document.createElement('table');
    table.className = 'platform-table';
    
    // Header
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    ['', 'Line', '', 'Time', 'In', 'Wait', 'Direction'].forEach(col => {
        const th = document.createElement('th');
        th.textContent = col;
        if (col) th.scope = 'col';
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);
    
    // Body
    const tbody = document.createElement('tbody');

    // Filter departures based on user filters
    const filteredDepartures = departures.filter(departure => filterDeparture(departure, walkTime));

    filteredDepartures.forEach(departure => {
        const row = document.createElement('tr');
        
        // Calculate time class
        const whenDate = new Date(departure.when);
        const minutesUntil = Math.floor((whenDate - new Date()) / (1000 * 60));
        row.className = getTimeClass(minutesUntil, timeConfig);
        
        // Transport type logo column
        const typeCell = document.createElement('td');
        typeCell.appendChild(createTransportLogo(departure.transport_type));
        row.appendChild(typeCell);

        // Line column
        const lineCell = document.createElement('td');
        lineCell.appendChild(createLineBadge(departure.line));
        row.appendChild(lineCell);

        // Direction emoji column
        const emojiCell = document.createElement('td');
        emojiCell.textContent = departure.direction_symbol;
        row.appendChild(emojiCell);

        // Time column (HH:MM)
        const timeCell = document.createElement('td');
        timeCell.textContent = whenDate.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        row.appendChild(timeCell);

        // Minutes until column
        const minsCell = document.createElement('td');
        minsCell.textContent = `${minutesUntil}m`;
        row.appendChild(minsCell);

        // Wait time column
        const waitCell = document.createElement('td');
        waitCell.textContent = `${departure.wait_time}m`;
        waitCell.className = departure.wait_time < 0 ? 'negative-wait' : '';
        row.appendChild(waitCell);

        // Direction column
        const dirCell = document.createElement('td');
        dirCell.textContent = departure.provenance;
        row.appendChild(dirCell);

        tbody.appendChild(row);
    });
    table.appendChild(tbody);
    return table;
}

// =============================================================================
// Station Rendering
// =============================================================================

// Store references to avoid adding duplicate event listeners
let scrollHandler = null;
let resizeHandler = null;

function renderStations(data) {
    if (!data?.stations?.length) {
        console.error('Invalid station data:', data);
        return;
    }

    const container = document.getElementById('stations-container');
    const navList = document.getElementById('station-nav-list');
    if (!container || !navList) {
        console.error('Could not find required containers');
        return;
    }
    
    // Clean up previous event listeners to prevent memory leak
    if (scrollHandler) container.removeEventListener('scroll', scrollHandler);
    if (resizeHandler) window.removeEventListener('resize', resizeHandler);
    
    container.innerHTML = '';
    navList.innerHTML = '';

    renderLastUpdated();
    
    // Create navigation items
    data.stations.forEach((station, idx) => {
        const navItem = document.createElement('li');
        navItem.className = 'station-nav-item';
        navItem.setAttribute('role', 'tab');
        navItem.setAttribute('aria-selected', idx === 0 ? 'true' : 'false');
        if (idx === 0) navItem.classList.add('active');
        
        const maxLen = CONFIG.STATION_NAME_MAX_LENGTH;
        navItem.textContent = station.name.length > maxLen 
            ? `${station.name.slice(0, maxLen)}...` 
            : station.name;
        
        navItem.addEventListener('click', () => {
            document.querySelectorAll('.station-nav-item').forEach((item, i) => {
                item.classList.toggle('active', item === navItem);
                item.setAttribute('aria-selected', item === navItem ? 'true' : 'false');
            });
            
            const stationElement = document.querySelector(`[data-station-id="${idx}"]`);
            if (stationElement) {
                container.scrollTo({ left: stationElement.offsetLeft, behavior: 'smooth' });
            }
        });
        
        navList.appendChild(navItem);
    });
    
    // Create station sections
    data.stations.forEach((station, idx) => {
        const section = document.createElement('section');
        section.className = 'station-section';
        section.dataset.stationId = idx;
        
        // Create header container
        const headerContainer = document.createElement('div');
        headerContainer.className = 'station-header-container';
        
        // Add station name
        const stationName = document.createElement('h2');
        stationName.className = 'station-header';
        stationName.textContent = station.name;
        headerContainer.appendChild(stationName);
        
        // Add walk time if available
        if (station.walkTime != null) {
            const walkTimeEl = document.createElement('div');
            walkTimeEl.className = 'station-walk-time';
            walkTimeEl.textContent = `${station.walkTime} min walk · ${formatDistance(station.distance)} away`;
            headerContainer.appendChild(walkTimeEl);
        }

        section.appendChild(headerContainer);

        // Add table with all departures
        const table = createStationTable(station.departures, station.timeConfig, station.walkTime);
        section.appendChild(table);
        
        container.appendChild(section);
    });

    // Update active station based on scroll position
    const updateActiveStation = () => {
        const sections = document.querySelectorAll('.station-section');
        let activeSection = null;
        let minDistance = Infinity;
        
        sections.forEach(section => {
            const distance = Math.abs(section.getBoundingClientRect().left);
            if (distance < minDistance) {
                minDistance = distance;
                activeSection = section;
            }
        });

        if (activeSection) {
            const idx = parseInt(activeSection.dataset.stationId, 10);
            document.querySelectorAll('.station-nav-item').forEach((item, i) => {
                const isActive = i === idx;
                item.classList.toggle('active', isActive);
                item.setAttribute('aria-selected', isActive ? 'true' : 'false');
            });
        }
    };

    // Create debounced scroll handler
    let scrollTimeout;
    scrollHandler = () => {
        clearTimeout(scrollTimeout);
        scrollTimeout = setTimeout(updateActiveStation, CONFIG.SCROLL_DEBOUNCE_MS);
    };
    resizeHandler = updateActiveStation;
    
    container.addEventListener('scroll', scrollHandler);
    window.addEventListener('resize', resizeHandler);
    
    updateActiveStation();
}

// =============================================================================
// Initialization
// =============================================================================

function showError(container, message) {
    if (container) {
        container.innerHTML = `<div class="error-message" role="alert">${message}</div>`;
    }
}

window.addEventListener('DOMContentLoaded', async () => {
    const container = document.getElementById('stations-container');
    const refreshButton = document.getElementById('refresh-button');

    async function refreshData(forceRefreshStations = false) {
        if (!refreshButton) return;
        
        refreshButton.classList.add('spinning');
        try {
            // Pass refresh flag to control station list refresh
            const data = await fetchStations(forceRefreshStations);
            state.lastData = data;
            state.lastUpdatedAt = new Date();
            renderLastUpdated();
            renderStations(data);
            
            // Trigger row sweep animation after render
            triggerRowAnimation();
        } catch (error) {
            console.error('Error during refresh:', error);
            showError(container, 'Error updating data. Please try again.');
        } finally {
            refreshButton.classList.remove('spinning');
        }
    }

    async function initialLoad() {
        try {
            // Initial page load refreshes stations
            const data = await fetchStations(true);
            state.lastData = data;
            state.lastUpdatedAt = new Date();
            renderStations(data);
            return true;
        } catch (error) {
            console.error('Error during initial load:', error);
            return false;
        }
    }

    // Set up refresh button - force refresh stations on manual click
    if (refreshButton) {
        refreshButton.addEventListener('click', () => refreshData(true));
    }

    // Update "last updated" display every second
    setInterval(renderLastUpdated, CONFIG.LAST_UPDATED_INTERVAL_MS);

    // Set up filter handlers
    const filterConfig = [
        { id: 'transportFilter', key: 'transport' },
        { id: 'directionFilter', key: 'direction' },
        { id: 'walkFilter', key: 'walkFilter' }
    ];
    
    filterConfig.forEach(({ id, key }) => {
        const el = document.getElementById(id);
        if (el) {
            state.filters[key] = el.value;
            el.addEventListener('change', () => {
                state.filters[key] = el.value;
                if (state.lastData) renderStations(state.lastData);
            });
        }
    });

    // Initial load
    await updateLocation();
    
    if (!await initialLoad()) {
        const retrySec = CONFIG.RETRY_DELAY_MS / 1000;
        showError(container, `Error loading station data. Retrying in ${retrySec} seconds...<br>You can also refresh the page to try again.`);
        
        setTimeout(async () => {
            if (!await initialLoad()) {
                const refreshSec = CONFIG.REFRESH_INTERVAL_MS / 1000;
                showError(container, `
                    Unable to load station data.<br>
                    Will automatically try again in ${refreshSec} seconds.<br>
                    You can also use the refresh button to try again immediately.
                `);
            }
        }, CONFIG.RETRY_DELAY_MS);
    }

    // Auto-refresh interval (don't refresh station list, only departures)
    const updateInterval = setInterval(() => refreshData(false), CONFIG.REFRESH_INTERVAL_MS);
    
    window.addEventListener('unload', () => {
        clearInterval(updateInterval);
    });
}); 