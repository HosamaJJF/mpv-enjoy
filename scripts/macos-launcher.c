#include <errno.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int launcher_error(const char *message)
{
    fprintf(stderr, "mpv-enjoy launcher: %s\n", message);
    return 1;
}

int main(int argc, char **argv)
{
    uint32_t executable_size = 0;
    if (_NSGetExecutablePath(NULL, &executable_size) != -1 || executable_size == 0)
        return launcher_error("could not determine executable path size");

    char *executable_path = malloc(executable_size);
    if (!executable_path)
        return launcher_error("out of memory");
    if (_NSGetExecutablePath(executable_path, &executable_size) != 0) {
        free(executable_path);
        return launcher_error("could not determine executable path");
    }

    char *filename = strrchr(executable_path, '/');
    if (!filename) {
        free(executable_path);
        return launcher_error("unexpected executable path");
    }
    *filename = '\0';

    static const char helper_suffix[] = "/../Resources/macos-launcher.sh";
    size_t helper_size = strlen(executable_path) + sizeof(helper_suffix);
    char *helper_path = malloc(helper_size);
    if (!helper_path) {
        free(executable_path);
        return launcher_error("out of memory");
    }
    snprintf(helper_path, helper_size, "%s%s", executable_path, helper_suffix);

    char **child_argv = calloc((size_t)argc + 2, sizeof(*child_argv));
    if (!child_argv) {
        free(helper_path);
        free(executable_path);
        return launcher_error("out of memory");
    }
    child_argv[0] = "/bin/sh";
    child_argv[1] = helper_path;
    for (int index = 1; index < argc; index++)
        child_argv[index + 1] = argv[index];

    execv(child_argv[0], child_argv);
    int saved_errno = errno;
    fprintf(stderr, "mpv-enjoy launcher: could not start helper: %s\n",
            strerror(saved_errno));
    free(child_argv);
    free(helper_path);
    free(executable_path);
    return 1;
}
