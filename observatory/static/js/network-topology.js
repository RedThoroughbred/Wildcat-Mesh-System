/**
 * Network Topology Visualization for Wildcat Mesh Observatory
 * Interactive force-directed graph showing mesh network connections
 */

class NetworkTopology {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        if (!this.container) return;

        this.options = {
            width: this.container.clientWidth || 800,
            height: options.height || 600,
            nodeRadius: 20,
            linkDistance: 150,
            chargeStrength: -300,
            ...options
        };

        this.svg = null;
        this.simulation = null;
        this.nodes = [];
        this.links = [];

        this.init();
    }

    init() {
        // Create SVG
        this.svg = d3.select(this.container)
            .append('svg')
            .attr('width', this.options.width)
            .attr('height', this.options.height)
            .style('background', 'var(--bg-surface)')
            .style('border-radius', '12px');

        // Add zoom behavior
        const zoom = d3.zoom()
            .scaleExtent([0.5, 3])
            .on('zoom', (event) => {
                this.svg.select('g').attr('transform', event.transform);
            });

        this.svg.call(zoom);

        // Create container group
        this.g = this.svg.append('g');

        // Add arrow markers for directed edges
        this.svg.append('defs').append('marker')
            .attr('id', 'arrowhead')
            .attr('viewBox', '-0 -5 10 10')
            .attr('refX', 25)
            .attr('refY', 0)
            .attr('orient', 'auto')
            .attr('markerWidth', 8)
            .attr('markerHeight', 8)
            .append('svg:path')
            .attr('d', 'M 0,-5 L 10 ,0 L 0,5')
            .attr('fill', '#666');

        // Create force simulation
        this.simulation = d3.forceSimulation()
            .force('link', d3.forceLink().id(d => d.id).distance(this.options.linkDistance))
            .force('charge', d3.forceManyBody().strength(this.options.chargeStrength))
            .force('center', d3.forceCenter(this.options.width / 2, this.options.height / 2))
            .force('collision', d3.forceCollide().radius(this.options.nodeRadius + 10));

        this.linkGroup = this.g.append('g').attr('class', 'links');
        this.nodeGroup = this.g.append('g').attr('class', 'nodes');
        this.labelGroup = this.g.append('g').attr('class', 'labels');
    }

    async loadData() {
        try {
            // Fetch nodes and neighbor information from API
            const [nodesResponse, neighborsResponse] = await Promise.all([
                fetch('/api/v1/nodes'),
                fetch('/api/v1/neighbor-info')
            ]);

            const nodesData = await nodesResponse.json();
            const neighborsData = await neighborsResponse.json();

            // Process nodes
            this.nodes = nodesData.map(node => ({
                id: node.sender_id,
                name: node.sender_short_name || node.sender_id,
                longName: node.sender_long_name,
                messages: node.message_count,
                snr: node.avg_snr || 0,
                status: this.getNodeStatus(node.last_seen)
            }));

            // Process links from neighbor data
            this.links = [];
            const linkMap = new Map();

            neighborsData.forEach(neighbor => {
                // Create unique link identifier (sorted to prevent duplicates)
                const linkId = [neighbor.node_id, neighbor.neighbor_id].sort().join('-');

                if (!linkMap.has(linkId)) {
                    linkMap.set(linkId, {
                        source: neighbor.node_id,
                        target: neighbor.neighbor_id,
                        snr: neighbor.snr || 0,
                        value: Math.abs(neighbor.snr || 0) // Link strength
                    });
                }
            });

            this.links = Array.from(linkMap.values());

            // If no neighbor data, create simple hub topology based on message patterns
            if (this.links.length === 0 && this.nodes.length > 0) {
                const hubNode = this.nodes.reduce((max, node) =>
                    node.messages > max.messages ? node : max
                , this.nodes[0]);

                this.nodes.forEach(node => {
                    if (node.id !== hubNode.id) {
                        this.links.push({
                            source: hubNode.id,
                            target: node.id,
                            snr: node.snr,
                            value: Math.abs(node.snr)
                        });
                    }
                });
            }

            this.render();
        } catch (error) {
            console.error('Error loading topology data:', error);
            if (window.toast) {
                toast.error('Failed to load network topology');
            }
        }
    }

    getNodeStatus(lastSeen) {
        if (!lastSeen) return 'offline';
        const hoursSince = (Date.now() / 1000 - lastSeen) / 3600;
        if (hoursSince < 1) return 'online';
        if (hoursSince < 24) return 'recent';
        return 'offline';
    }

    getNodeColor(node) {
        const colors = {
            online: '#4CAF50',
            recent: '#FF9800',
            offline: '#666666'
        };
        return colors[node.status] || colors.offline;
    }

    getLinkColor(link) {
        const snr = link.snr || 0;
        if (snr > 5) return '#4CAF50';
        if (snr > 0) return '#FF9800';
        return '#F44336';
    }

    render() {
        // Render links
        const link = this.linkGroup.selectAll('line')
            .data(this.links)
            .join('line')
            .attr('stroke', d => this.getLinkColor(d))
            .attr('stroke-width', d => Math.max(1, d.value / 2))
            .attr('stroke-opacity', 0.6)
            .attr('marker-end', 'url(#arrowhead)');

        // Render nodes
        const node = this.nodeGroup.selectAll('g')
            .data(this.nodes)
            .join('g')
            .call(this.drag());

        // Node circles
        node.append('circle')
            .attr('r', this.options.nodeRadius)
            .attr('fill', d => this.getNodeColor(d))
            .attr('stroke', '#fff')
            .attr('stroke-width', 2)
            .style('cursor', 'pointer');

        // Node labels
        const label = this.labelGroup.selectAll('text')
            .data(this.nodes)
            .join('text')
            .text(d => d.name)
            .attr('font-size', '12px')
            .attr('fill', '#fff')
            .attr('text-anchor', 'middle')
            .attr('dy', this.options.nodeRadius + 15)
            .style('pointer-events', 'none');

        // Add tooltips
        node.append('title')
            .text(d => `${d.longName || d.name}\nMessages: ${d.messages}\nSNR: ${d.snr.toFixed(1)} dB\nStatus: ${d.status}`);

        // Update simulation
        this.simulation.nodes(this.nodes);
        this.simulation.force('link').links(this.links);

        this.simulation.on('tick', () => {
            link
                .attr('x1', d => d.source.x)
                .attr('y1', d => d.source.y)
                .attr('x2', d => d.target.x)
                .attr('y2', d => d.target.y);

            node
                .attr('transform', d => `translate(${d.x},${d.y})`);

            label
                .attr('x', d => d.x)
                .attr('y', d => d.y);
        });

        this.simulation.alpha(1).restart();

        if (window.toast) {
            toast.success(`Loaded ${this.nodes.length} nodes and ${this.links.length} connections`, 3000);
        }
    }

    drag() {
        function dragstarted(event, d) {
            if (!event.active) this.simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
        }

        function dragged(event, d) {
            d.fx = event.x;
            d.fy = event.y;
        }

        function dragended(event, d) {
            if (!event.active) this.simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
        }

        return d3.drag()
            .on('start', dragstarted.bind(this))
            .on('drag', dragged.bind(this))
            .on('end', dragended.bind(this));
    }

    refresh() {
        this.loadData();
    }

    destroy() {
        if (this.simulation) {
            this.simulation.stop();
        }
        if (this.svg) {
            this.svg.remove();
        }
    }
}

// Export to global scope
window.NetworkTopology = NetworkTopology;
