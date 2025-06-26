// Cache transport logos
const transportLogoCache = {};

// Cache line badges
const lineBadgeCache = {};

// Global config object to store station data
let globalConfig = null;

// Global state for filters
let currentFilters = {
    transport: 'all',
    direction: 'all',
    walkTimeEnabled: true
};

async function fetchStations() {
    try {
        console.log('Fetching station data...');
        const resp = await fetch('/api/stations', {
            // Add timeout using AbortController
            signal: AbortSignal.timeout(10000)
        });
        if (!resp.ok) {
            throw new Error(`HTTP error! status: ${resp.status}`);
        }
        const data = await resp.json();
        globalConfig = data.config; // Store global config
        console.log('Successfully fetched station data');
        return data;
    } catch (error) {
        console.error('Error fetching stations:', error);
        throw error;
    }
}

function formatLastUpdated() {
    const now = new Date();
    return now.toLocaleTimeString([], { 
        hour: '2-digit', 
        minute: '2-digit',
        second: '2-digit'
    });
}

function formatCurrentTime() {
    const now = new Date();
    return now.toLocaleTimeString([], { 
        hour: '2-digit', 
        minute: '2-digit',
        second: '2-digit'
    });
}

function updateCurrentTime() {
    const timeDisplay = document.querySelector('.current-time');
    if (timeDisplay) {
        timeDisplay.textContent = formatCurrentTime();
    }
}

function formatTime(when) {
    const date = new Date(when);
    const now = new Date();
    const diffMins = Math.round((date - now) / 60000);
    const hhmm = date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
    return `${hhmm} (${diffMins}m)`;
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
    // Use cached logo if available
    if (transportLogoCache[type]) {
        return transportLogoCache[type].cloneNode(true);
    }

    const img = document.createElement('img');
    img.className = 'transport-logo';
    
    switch(type) {
        case 'S-Bahn':
            img.src = 'https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/S-Bahn-Logo.svg/2048px-S-Bahn-Logo.svg.png';
            break;
        case 'U-Bahn':
            img.src = 'https://upload.wikimedia.org/wikipedia/commons/e/ee/U-Bahn_Berlin_logo.svg';
            break;
        case 'Tram':
            img.src = 'https://upload.wikimedia.org/wikipedia/commons/a/a6/Tram-Logo.svg';
            break;
        case 'Bus':
            img.src = 'https://upload.wikimedia.org/wikipedia/commons/thumb/8/83/BUS-Logo-BVG.svg/1024px-BUS-Logo-BVG.svg.png';
            break;
        case 'DB':
            img.src = 'https://upload.wikimedia.org/wikipedia/commons/d/d5/Deutsche_Bahn_AG-Logo.svg';
            break;
    }
    img.alt = type;
    
    transportLogoCache[type] = img;
    return img.cloneNode(true);
}

function filterDeparture(departure, walkTime) {
    // Transport type filter
    if (currentFilters.transport !== 'all' && departure.transport_type !== currentFilters.transport) {
        return false;
    }
    
    // Direction filter
    if (currentFilters.direction !== 'all' && departure.direction_symbol !== currentFilters.direction) {
        return false;
    }

    // Walk time filter
    if (currentFilters.walkTimeEnabled && walkTime !== null) {
        const whenDate = new Date(departure.when);
        const minutesUntil = Math.floor((whenDate - new Date()) / (1000 * 60));
        const walkTimeBuffer = 4; // Buffer time before walk time
        const minTimeNeeded = walkTime - walkTimeBuffer;
        
        // Filter out trains that don't give enough time to walk there
        // i.e., keep only trains where we have enough time to walk there
        return minutesUntil > minTimeNeeded;
    }
    
    return true;
}

function createStationTable(departures, timeConfig, walkTime) {
    const table = document.createElement('table');
    table.className = 'platform-table';
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    ['', 'Line', '', 'Time', 'In', 'Wait', 'Direction'].forEach(col => {
        const th = document.createElement('th');
        th.textContent = col;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);
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

function renderStations(data) {
    if (!data || !data.stations || !Array.isArray(data.stations)) {
        console.error('Invalid station data:', data);
        return;
    }

    const container = document.getElementById('stations-container');
    const navList = document.getElementById('station-nav-list');
    if (!container || !navList) {
        console.error('Could not find required containers');
        return;
    }
    
    container.innerHTML = '';
    navList.innerHTML = '';

    // Update the timestamp in the dedicated container
    const updateInfo = document.querySelector('.update-info');
    if (updateInfo) {
        updateInfo.textContent = formatLastUpdated();
    }
    
    // Create navigation items
    data.stations.forEach((station, idx) => {
        const navItem = document.createElement('li');
        navItem.className = 'station-nav-item';
        if (idx === 0) navItem.classList.add('active');
        
        const truncatedName = station.name.length > 20 ? `${station.name.slice(0, 20)}...` : station.name;
        navItem.textContent = truncatedName;
        
        navItem.addEventListener('click', () => {
            // Update active state
            document.querySelectorAll('.station-nav-item').forEach(item => item.classList.remove('active'));
            navItem.classList.add('active');
            
            // Scroll to station without scrolling to top
            const stationElement = document.querySelector(`[data-station-id="${idx}"]`);
            if (stationElement) {
                container.scrollTo({
                    left: stationElement.offsetLeft,
                    behavior: 'smooth'
                });
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
        
        // // Add station name
        // const header = document.createElement('h2');
        // header.className = 'station-header';
        // header.textContent = station.name;
        // headerContainer.appendChild(header);

        // Add walk time if available
        if (station.walkTime !== null && station.walkTime !== undefined) {
            const walkTime = document.createElement('div');
            walkTime.className = 'station-walk-time';
            walkTime.textContent = `${station.walkTime} minute walk`;
            headerContainer.appendChild(walkTime);
        }

        section.appendChild(headerContainer);

        // Add table with all departures
        const table = createStationTable(station.departures, station.timeConfig, station.walkTime);
        section.appendChild(table);
        
        container.appendChild(section);
    });

    // Add scroll listener to update active nav item
    const updateActiveStation = () => {
        const sections = document.querySelectorAll('.station-section');
        let activeSection = null;
        let minDistance = Infinity;
        
        sections.forEach(section => {
            const rect = section.getBoundingClientRect();
            const distance = Math.abs(rect.left);
            if (distance < minDistance) {
                minDistance = distance;
                activeSection = section;
            }
        });

        if (activeSection) {
            const idx = activeSection.dataset.stationId;
            document.querySelectorAll('.station-nav-item').forEach((item, i) => {
                item.classList.toggle('active', i === parseInt(idx));
            });
        }
    };

    // Update active station on scroll
    let scrollTimeout;
    container.addEventListener('scroll', () => {
        clearTimeout(scrollTimeout);
        scrollTimeout = setTimeout(updateActiveStation, 50);  // Debounce the scroll event
    });

    // Also update on any resize events
    window.addEventListener('resize', updateActiveStation);

    // Initial update
    updateActiveStation();
}

// Initialize the app
window.addEventListener('DOMContentLoaded', async () => {
    console.log('Page loaded, initializing station data...');
    const retryDelay = 5000;  // 5 second retry for initial load
    const refreshInterval = 30000;  // 30 second refresh interval
    let lastData = null;
    const container = document.getElementById('stations-container');
    const refreshButton = document.getElementById('refresh-button');

    // Function to get and send location
    async function updateLocation() {
        if ("geolocation" in navigator) {
            try {
                const position = await new Promise((resolve, reject) => {
                    navigator.geolocation.getCurrentPosition(resolve, reject);
                });
                
                await fetch('/api/location', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        latitude: position.coords.latitude,
                        longitude: position.coords.longitude
                    })
                });
                return true;
            } catch (error) {
                console.error("Error getting location:", error);
                return false;
            }
        }
        return false;
    }

    // Function to refresh data with visual feedback
    async function refreshData() {
        if (!refreshButton) return;
        
        refreshButton.classList.add('spinning');
        try {
            // Update location first
            await updateLocation();
            // Then fetch new data
            const data = await fetchStations();
            lastData = data;
            renderStations(data);
            console.log('Refresh complete');
        } catch (error) {
            console.error('Error during refresh:', error);
            showError('Error updating data. Please try again.');
        } finally {
            refreshButton.classList.remove('spinning');
        }
    }

    // Add click handler for refresh button
    if (refreshButton) {
        refreshButton.addEventListener('click', refreshData);
    }

    // Start updating current time
    updateCurrentTime();
    setInterval(updateCurrentTime, 1000);

    // Add filter handlers
    const transportFilter = document.getElementById('transportFilter');
    const directionFilter = document.getElementById('directionFilter');
    const walkTimeFilter = document.getElementById('walkTimeFilter');
    let walkTimeEnabled = true; // Start with enabled

    transportFilter.addEventListener('change', () => {
        currentFilters.transport = transportFilter.value;
        if (lastData) {
            renderStations(lastData);
        }
    });

    directionFilter.addEventListener('change', () => {
        currentFilters.direction = directionFilter.value;
        if (lastData) {
            renderStations(lastData);
        }
    });

    walkTimeFilter.addEventListener('click', () => {
        walkTimeEnabled = !walkTimeEnabled;
        walkTimeFilter.classList.toggle('active', walkTimeEnabled);
        currentFilters.walkTimeEnabled = walkTimeEnabled;
        if (lastData) {
            renderStations(lastData);
        }
    });

    // Function to show error message
    const showError = (message) => {
        if (container) {
            container.innerHTML = `
                <div class="error-message">
                    ${message}
                </div>
            `;
        }
    };

    // Initial load with one retry
    async function initialLoad() {
        try {
            const data = await fetchStations();
            lastData = data;
            renderStations(data);
            console.log('Initial render complete');
            return true;
        } catch (error) {
            console.error('Error during initial load:', error);
            return false;
        }
    }

    // Initial location update and data load
    await updateLocation();
    if (!await initialLoad()) {
        showError(`Error loading station data. Retrying in ${retryDelay/1000} seconds...<br>You can also refresh the page to try again.`);
        
        // Try one more time after delay
        setTimeout(async () => {
            if (!await initialLoad()) {
                showError(`
                    Unable to load station data.<br>
                    Will automatically try again in ${refreshInterval/1000} seconds.<br>
                    You can also use the refresh button to try again immediately.
                `);
            }
        }, retryDelay);
    }

    // Set up automatic refresh interval
    const updateInterval = setInterval(refreshData, refreshInterval);
    
    window.addEventListener('unload', () => {
        if (updateInterval) clearInterval(updateInterval);
    });
}); 