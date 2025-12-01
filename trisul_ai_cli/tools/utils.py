import matplotlib.pyplot as plt
from datetime import datetime
from matplotlib.ticker import FuncFormatter
import matplotlib.dates as mdates
import ast
import json
import re


class TrisulAIUtils:
    """Utility class for Trisul AI CLI"""
    def __init__(self,logging=None):
        self.logging = logging

    
    def bytes_to_human(self, num, as_string=True):
        """Convert bytes to human-readable format."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if num < 1024:
                if as_string:
                    return f"{num:.2f} {unit}"
                return num, unit
            num /= 1024
        
        if as_string:
            return f"{num:.2f} PB"
        return num, "PB"
    
    

    # LINE CHART
    
    async def display_line_chart(self, line_chart_data, file_path=None):
        self.logging.info("[Utils] [display_line_chart] Generating the line chart")

        # Convert JSON string → dict if needed
        if isinstance(line_chart_data, str):
            try:
                data = ast.literal_eval(line_chart_data)
            except Exception:
                try:
                    data = json.loads(line_chart_data)
                except Exception:
                    self.logging.error("[Utils] [display_line_chart] Invalid JSON value from LLM")
                    return
        elif isinstance(line_chart_data, dict):
            data = line_chart_data
        else:
            self.logging.error("[Utils] [display_line_chart] Invalid line chart data format. Expected dict or JSON string.")
            return





        fig, ax = plt.subplots(figsize=(12, 6))
        scatter_points = []
        all_values = []  # collect all values to find best axis scale

        for series in data.get("keys", []):
            # Convert epoch seconds → datetime
            timestamps = [datetime.fromtimestamp(ts) for ts in series["timestamps"]]
            values = series["values"]
            all_values.extend(values)

            line, = ax.plot(
                timestamps,
                values,
                label=series["legend_label"],
                color=series.get("color", None),
                marker='o'
            )
            scatter_points.append((line, timestamps, values))

        # Determine global scale for axis
        max_val = max(all_values)
        scaled_val, unit = self.bytes_to_human(max_val, as_string=False)
        scale_factor = max_val / scaled_val  # bytes per displayed unit

        # Apply formatter to y-axis
        ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y / scale_factor:.2f} {unit}"))

        # Format the x-axis as date/time
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M:%S'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())

        ax.set_title(data.get("title", "Traffic Chart"))
        ax.set_xlabel(data.get("x_label", "Time"))
        ax.set_ylabel(f"{data.get('y_label', 'Traffic')} ({unit})")
        ax.legend()
        ax.grid(True)
        fig.autofmt_xdate()
        
        # Create annotation (tooltip)
        annot = ax.annotate(
            "", xy=(0,0), xytext=(20,20), textcoords="offset points",
            bbox=dict(boxstyle="round", fc="w"),
            arrowprops=dict(arrowstyle="->")
        )
        annot.set_visible(False)

        def update_annot(ind, line, x_data, y_data):
            x = x_data[ind]
            y = y_data[ind]
            annot.xy = (x, y)
            text = f"{x.strftime('%Y-%m-%d %H:%M:%S')}\n{self.bytes_to_human(y)}"
            annot.set_text(text)
            annot.get_bbox_patch().set_facecolor(line.get_color())
            annot.get_bbox_patch().set_alpha(0.6)

        def hover(event):
            visible = annot.get_visible()
            if event.inaxes == ax:
                for line, x_data, y_data in scatter_points:
                    cont, ind = line.contains(event)
                    if cont:
                        update_annot(ind["ind"][0], line, x_data, y_data)
                        annot.set_visible(True)
                        fig.canvas.draw_idle()
                        return
            if visible:
                annot.set_visible(False)
                fig.canvas.draw_idle()

        fig.canvas.mpl_connect("motion_notify_event", hover)
        plt.tight_layout()
        
        if(file_path):
            plt.savefig(file_path, dpi=300, bbox_inches='tight')
            plt.close()
            self.logging.info(f"[Utils] [display_line_chart] Chart saved to {file_path}")
        else:
            self.logging.info("[Utils] [display_line_chart] Chart UI ready. Awaiting user interaction")
            plt.show()
            plt.close()
            self.logging.info("[Utils] [display_line_chart] Chart closed by user")
            print("🤖 (Bot) : Chart Closed\n")

            



    # PIE CHART

    async def display_pie_chart(self, pie_chart_data, file_path=None):
        self.logging.info("[Utils] [display_pie_chart] Starting pie chart render workflow")

        raw_input_type = type(pie_chart_data).__name__
        self.logging.info("[Utils] [display_pie_chart] Inbound chart data type=%s", raw_input_type)

        chart_opts = pie_chart_data

        # Normalize data to dict
        if isinstance(pie_chart_data, str):
            self.logging.info("[Utils] [display_pie_chart] Attempt JSON parsing for string input")
            try:
                chart_opts = json.loads(pie_chart_data)
                self.logging.info("[Utils] [display_pie_chart] Chart config loaded from JSON string")
            except json.JSONDecodeError:
                self.logging.warning("[Utils] [display_pie_chart] Non-standard JSON received. Attempting normalization")
                normalized = pie_chart_data.strip()
                normalized = re.sub(r"(?<!\\)'", '"', normalized)
                normalized = re.sub(r",\s*}", "}", normalized)
                normalized = re.sub(r",\s*]", "]", normalized)

                try:
                    chart_opts = json.loads(normalized)
                    self.logging.info("[Utils] [display_pie_chart] Chart config normalized and parsed successfully")
                except json.JSONDecodeError as e:
                    self.logging.error("[Utils] [display_pie_chart] Normalization failed. Root cause=%s", e)
                    raise ValueError(f"Invalid chart data format after normalization: {e}")

        elif isinstance(pie_chart_data, dict):
            self.logging.info("[Utils] [display_pie_chart] Chart config loaded from dict")
        else:
            self.logging.error("[Utils] [display_pie_chart] Unsupported data type for pie_chart_data: %s", raw_input_type)
            raise TypeError("pie_chart_data must be a dict or JSON string")

        labels = chart_opts.get('labels', [])
        volumes = chart_opts.get('volumes', [])
        colors = chart_opts.get('colors', [])
        chart_title = chart_opts.get('chart_title', "Pie Chart")
        legend_title = chart_opts.get('legend_title', "Legend")

        self.logging.info("[Utils] [display_pie_chart] Chart metadata loaded: labels=%d volumes=%d colors=%d",
                    len(labels), len(volumes), len(colors))

        total_volume = sum(volumes)
        if total_volume == 0:
            self.logging.warning("[Utils] [display_pie_chart] All volume values are zero. Chart aborted.")
            return

        self.logging.info("[Utils] [display_pie_chart] Rendering chart: title='%s' total_items=%d total_volume=%s",
                    chart_title, len(volumes), self.bytes_to_human(total_volume))

        fig, ax = plt.subplots(figsize=(7, 6))
        wedges, texts = ax.pie(
            volumes,
            labels=labels,
            colors=colors,
            startangle=90,
            labeldistance=0.7,
            wedgeprops=dict(edgecolor='none')
        )

        ax.axis('equal')
        plt.title(chart_title, pad=20)
        legend = ax.legend(
            wedges,
            labels,
            loc="center left",
            bbox_to_anchor=(1.05, 0.5),
            frameon=False,
            title=legend_title
        )

        self.logging.info("[Utils] [display_pie_chart] Hover and click event handlers initializing")

        # Tooltip
        tooltip = ax.annotate(
            "",
            xy=(0, 0),
            xytext=(15, 15),
            textcoords="offset points",
            ha='left', va='bottom',
            fontsize=10, fontweight='bold', color='black',
            bbox=dict(facecolor='white', alpha=0.9, boxstyle='round', ec='gray'),
            visible=False
        )

        hovered_index = {'value': None}
        selected_index = {'value': None}

        def on_motion(event):
            # Keep logging light inside event loop
            found = False
            if event.inaxes != ax:
                if hovered_index['value'] is not None:
                    for w in wedges:
                        w.set_alpha(1.0)
                    tooltip.set_visible(False)
                    hovered_index['value'] = None
                    fig.canvas.draw_idle()
                return

            for i, w in enumerate(wedges):
                contains, _ = w.contains(event)
                if contains:
                    if hovered_index['value'] != i:
                        for ww in wedges:
                            ww.set_alpha(0.6)
                        w.set_alpha(1.0)
                        hovered_index['value'] = i
                    tooltip.xy = (event.xdata, event.ydata)
                    tooltip.set_text(f"{labels[i]}: {self.bytes_to_human(volumes[i])}")
                    tooltip.set_visible(True)
                    fig.canvas.draw_idle()
                    found = True
                    break

            if not found:
                renderer = fig.canvas.get_renderer()
                for i, leg_text in enumerate(legend.get_texts()):
                    bbox = leg_text.get_window_extent(renderer=renderer)
                    if bbox.contains(event.x, event.y):
                        if hovered_index['value'] != i:
                            for ww in wedges:
                                ww.set_alpha(0.6)
                            wedges[i].set_alpha(1.0)
                            hovered_index['value'] = i
                        tooltip.xy = (event.xdata, event.ydata)
                        tooltip.set_text(f"{labels[i]}: {self.bytes_to_human(volumes[i])}")
                        tooltip.set_visible(True)
                        fig.canvas.draw_idle()
                        found = True
                        break

            if not found and hovered_index['value'] is not None:
                for ww in wedges:
                    ww.set_alpha(1.0)
                tooltip.set_visible(False)
                hovered_index['value'] = None
                fig.canvas.draw_idle()

        def on_click(event):
            renderer = fig.canvas.get_renderer()
            for i, leg_text in enumerate(legend.get_texts()):
                bbox = leg_text.get_window_extent(renderer=renderer)
                if bbox.contains(event.x, event.y):
                    self.logging.info("[Utils] [display_pie_chart] Legend item clicked index=%d label='%s'", i, labels[i])
                    for w in wedges:
                        w.set_center((0, 0))
                        w.set_alpha(0.8)
                        w.set_radius(1.0)

                    if selected_index['value'] == i:
                        self.logging.info("[Utils] [display_pie_chart] Slice deselected index=%d", i)
                        selected_index['value'] = None
                        fig.canvas.draw_idle()
                        return

                    w = wedges[i]
                    w.set_radius(1.1)
                    w.set_alpha(1.0)
                    selected_index['value'] = i
                    fig.canvas.draw_idle()
                    break

        fig.canvas.mpl_connect("motion_notify_event", on_motion)
        fig.canvas.mpl_connect("button_press_event", on_click)

        plt.tight_layout()
        
        
        if(file_path):
            plt.savefig(file_path, dpi=300, bbox_inches='tight')
            plt.close()
            self.logging.info(f"[Utils] [display_pie_chart] Chart saved to {file_path}")

        else:
            self.logging.info("[Utils] [display_pie_chart] Chart UI ready. Awaiting user interaction")
            plt.show()
            plt.close()
            self.logging.info("[Utils] [display_pie_chart] Chart closed by user")
            print("🤖 (Bot) : Chart Closed\n")
            

