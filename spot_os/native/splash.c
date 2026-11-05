/* Independent layer-shell overlay. ACK comes ONLY from wp_presentation.presented.
 * No time-based cover ACK, no root, no desktop and no update implementation here. */
#include <gtk/gtk.h>
#include <gtk-layer-shell.h>
#include <gdk/gdkwayland.h>
#include <wayland-client.h>
#include <stdio.h>
#include <string.h>
#include "presentation.h"
#include "foreign.h"

static GtkWidget *window;
static GtkWidget *background;
static cairo_surface_t *image;
static struct wp_presentation *presentation;
static struct zwlr_foreign_toplevel_manager_v1 *foreign_manager;
static unsigned generation = 1;
static gboolean recovery = FALSE, awaiting = FALSE;

/* Exposure is intentionally false for an occluded Qt window. Independently
 * attest mapping/fullscreen through the compositor's foreign-toplevel list. */
struct app { struct zwlr_foreign_toplevel_handle_v1 *handle; char *id; gboolean fullscreen; };
static GSList *apps;
static int mapped_count = -1;
static void mapped_changed(void) {
    int count = 0;
    for (GSList *p = apps; p; p = p->next) {
        struct app *app = p->data;
        if (app->id && strcmp(app->id, "spot-app") == 0 && app->fullscreen) count++;
    }
    if (count != mapped_count) { mapped_count = count; printf("toplevel %d\n", count); fflush(stdout); }
}
static void top_title(void *data, struct zwlr_foreign_toplevel_handle_v1 *h, const char *title) {}
static void top_appid(void *data, struct zwlr_foreign_toplevel_handle_v1 *h, const char *id) {
    struct app *app = data; g_free(app->id); app->id = g_strdup(id);
}
static void top_output(void *data, struct zwlr_foreign_toplevel_handle_v1 *h, struct wl_output *output) {}
static void top_state(void *data, struct zwlr_foreign_toplevel_handle_v1 *h, struct wl_array *states) {
    struct app *app = data; app->fullscreen = FALSE; uint32_t *value;
    wl_array_for_each(value, states) {
        if (*value == ZWLR_FOREIGN_TOPLEVEL_HANDLE_V1_STATE_FULLSCREEN) app->fullscreen = TRUE;
    }
}
static void top_done(void *data, struct zwlr_foreign_toplevel_handle_v1 *h) { mapped_changed(); }
static void top_closed(void *data, struct zwlr_foreign_toplevel_handle_v1 *h) {
    struct app *app = data; apps = g_slist_remove(apps, app);
    zwlr_foreign_toplevel_handle_v1_destroy(h); g_free(app->id); g_free(app); mapped_changed();
}
static void top_parent(void *data, struct zwlr_foreign_toplevel_handle_v1 *h,
                       struct zwlr_foreign_toplevel_handle_v1 *parent) {}
static const struct zwlr_foreign_toplevel_handle_v1_listener top_listener = {
    .title = top_title, .app_id = top_appid, .output_enter = top_output, .output_leave = top_output,
    .state = top_state, .done = top_done, .closed = top_closed, .parent = top_parent
};
static void top_created(void *data, struct zwlr_foreign_toplevel_manager_v1 *manager,
                        struct zwlr_foreign_toplevel_handle_v1 *handle) {
    struct app *app = g_new0(struct app, 1); app->handle = handle;
    apps = g_slist_prepend(apps, app); zwlr_foreign_toplevel_handle_v1_add_listener(handle, &top_listener, app);
}
static void top_finished(void *data, struct zwlr_foreign_toplevel_manager_v1 *manager) { gtk_main_quit(); }
static const struct zwlr_foreign_toplevel_manager_v1_listener manager_listener = { top_created, top_finished };

struct ticket { unsigned generation; };
static void feedback_output(void *data, struct wp_presentation_feedback *f, struct wl_output *o) {}
static void feedback_presented(void *data, struct wp_presentation_feedback *f,
    uint32_t hi, uint32_t lo, uint32_t ns, uint32_t refresh, uint32_t seqhi, uint32_t seqlo, uint32_t flags) {
    struct ticket *ticket = data;
    if (ticket->generation == generation && gtk_widget_get_visible(window)) {
        printf("covered %u\n", generation); fflush(stdout); awaiting = FALSE;
    }
    wp_presentation_feedback_destroy(f); g_free(ticket);
}
static void feedback_discarded(void *data, struct wp_presentation_feedback *f) {
    g_free(data); wp_presentation_feedback_destroy(f);
    awaiting = FALSE; gtk_widget_queue_draw(window);
}
static const struct wp_presentation_feedback_listener feedback_listener = {
    feedback_output, feedback_presented, feedback_discarded
};
static void registry_global(void *data, struct wl_registry *registry, uint32_t name,
                            const char *interface, uint32_t version) {
    if (strcmp(interface, "wp_presentation") == 0)
        presentation = wl_registry_bind(registry, name, &wp_presentation_interface, 1);
    if (strcmp(interface, "zwlr_foreign_toplevel_manager_v1") == 0) {
        foreign_manager = wl_registry_bind(registry, name, &zwlr_foreign_toplevel_manager_v1_interface,
                                           version < 3 ? version : 3);
        zwlr_foreign_toplevel_manager_v1_add_listener(foreign_manager, &manager_listener, NULL);
    }
}
static void registry_remove(void *data, struct wl_registry *registry, uint32_t name) {}
static const struct wl_registry_listener registry_listener = { registry_global, registry_remove };

static gboolean draw(GtkWidget *widget, cairo_t *cr, gpointer data) {
    if (gtk_widget_get_allocated_width(widget) != 800 || gtk_widget_get_allocated_height(widget) != 480) {
        g_printerr("SPOT display must be 800x480 at scale 1\n"); gtk_main_quit(); return FALSE;
    }
    cairo_set_source_rgb(cr, 1, 1, 1); cairo_paint(cr);
    cairo_set_source_surface(cr, image, 0, 0); cairo_paint(cr);
    if (recovery && data == window) {
        cairo_set_source_rgb(cr, 0.15, 0.15, 0.15);
        cairo_select_font_face(cr, "sans", CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL);
        cairo_set_font_size(cr, 16);
        const char *text = "SPOT Wiederherstellung - Wartungszugang verwenden";
        cairo_text_extents_t ext; cairo_text_extents(cr, text, &ext);
        cairo_move_to(cr, (800 - ext.width) / 2, 430); cairo_show_text(cr, text);
    }
    if (data == window && !awaiting) {
        struct wl_surface *surface = gdk_wayland_window_get_wl_surface(gtk_widget_get_window(window));
        struct wp_presentation_feedback *f = wp_presentation_feedback(presentation, surface);
        struct ticket *ticket = g_new(struct ticket, 1); ticket->generation = generation;
        wp_presentation_feedback_add_listener(f, &feedback_listener, ticket); awaiting = TRUE;
    }
    return TRUE;
}
static gboolean input(GIOChannel *channel, GIOCondition condition, gpointer data) {
    if (condition & (G_IO_HUP | G_IO_ERR)) { gtk_main_quit(); return FALSE; }
    gchar *line = NULL; gsize length = 0;
    if (g_io_channel_read_line(channel, &line, &length, NULL, NULL) != G_IO_STATUS_NORMAL) return TRUE;
    unsigned id = 0;
    if (length < 80 && sscanf(line, "cover %u", &id) == 1) {
        generation = id; awaiting = FALSE; recovery = FALSE;
        gtk_widget_show_all(window); gtk_widget_queue_draw(window);
    } else if (length < 80 && sscanf(line, "reveal %u", &id) == 1 && id == generation) {
        gtk_widget_hide(window);
    } else if (length < 80 && sscanf(line, "recovery %u", &id) == 1) {
        generation = id; recovery = TRUE; awaiting = FALSE;
        gtk_widget_show_all(window); gtk_widget_queue_draw(window);
    } else if (strcmp(line, "quit\n") == 0) gtk_main_quit();
    g_free(line); return TRUE;
}
static void realized(GtkWidget *widget, gpointer data) {
    GdkCursor *cursor = gdk_cursor_new_for_display(gtk_widget_get_display(widget), GDK_BLANK_CURSOR);
    gdk_window_set_cursor(gtk_widget_get_window(widget), cursor); g_object_unref(cursor);
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    image = cairo_image_surface_create_from_png(argv[1]);
    if (cairo_surface_status(image) != CAIRO_STATUS_SUCCESS ||
        cairo_image_surface_get_width(image) != 800 || cairo_image_surface_get_height(image) != 480) return 3;
    gtk_init(&argc, &argv);
    if (!GDK_IS_WAYLAND_DISPLAY(gdk_display_get_default()) || !gtk_layer_is_supported()) return 4;
    struct wl_display *display = gdk_wayland_display_get_wl_display(gdk_display_get_default());
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL); wl_display_roundtrip(display);
    if (!presentation) { g_printerr("Compositor lacks wp_presentation\n"); return 5; }
    if (!foreign_manager) { g_printerr("Compositor lacks foreign-toplevel mapping proof\n"); return 6; }
    /* Always keep the same logo behind the app. If Qt crashes, the compositor
     * exposes this surface immediately, before the Python supervisor can react. */
    background = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_layer_init_for_window(GTK_WINDOW(background));
    gtk_layer_set_namespace(GTK_WINDOW(background), "spot-os-base");
    gtk_layer_set_layer(GTK_WINDOW(background), GTK_LAYER_SHELL_LAYER_BACKGROUND);
    gtk_layer_set_exclusive_zone(GTK_WINDOW(background), -1);
    gtk_layer_set_keyboard_mode(GTK_WINDOW(background), GTK_LAYER_SHELL_KEYBOARD_MODE_NONE);
    for (int edge = 0; edge < GTK_LAYER_SHELL_EDGE_ENTRY_NUMBER; edge++)
        gtk_layer_set_anchor(GTK_WINDOW(background), edge, TRUE);
    gtk_widget_set_app_paintable(background, TRUE);
    GtkWidget *base_drawing = gtk_drawing_area_new();
    gtk_container_add(GTK_CONTAINER(background), base_drawing);
    g_signal_connect(base_drawing, "draw", G_CALLBACK(draw), background);
    g_signal_connect(base_drawing, "realize", G_CALLBACK(realized), NULL);
    g_signal_connect(background, "realize", G_CALLBACK(realized), NULL);
    gtk_widget_show_all(background);
    window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_layer_init_for_window(GTK_WINDOW(window));
    gtk_layer_set_namespace(GTK_WINDOW(window), "spot-os-splash");
    gtk_layer_set_layer(GTK_WINDOW(window), GTK_LAYER_SHELL_LAYER_OVERLAY);
    gtk_layer_set_exclusive_zone(GTK_WINDOW(window), -1);
    gtk_layer_set_keyboard_mode(GTK_WINDOW(window), GTK_LAYER_SHELL_KEYBOARD_MODE_EXCLUSIVE);
    for (int edge = 0; edge < GTK_LAYER_SHELL_EDGE_ENTRY_NUMBER; edge++)
        gtk_layer_set_anchor(GTK_WINDOW(window), edge, TRUE);
    gtk_widget_set_app_paintable(window, TRUE);
    GtkWidget *drawing = gtk_drawing_area_new();
    gtk_container_add(GTK_CONTAINER(window), drawing);
    g_signal_connect(drawing, "draw", G_CALLBACK(draw), window);
    g_signal_connect(drawing, "realize", G_CALLBACK(realized), NULL);
    g_signal_connect(window, "realize", G_CALLBACK(realized), NULL);
    g_signal_connect(window, "destroy", G_CALLBACK(gtk_main_quit), NULL);
    GIOChannel *channel = g_io_channel_unix_new(0);
    g_io_add_watch(channel, G_IO_IN | G_IO_HUP | G_IO_ERR, input, NULL);
    gtk_widget_show_all(window); gtk_main();
    cairo_surface_destroy(image); return 0;
}
