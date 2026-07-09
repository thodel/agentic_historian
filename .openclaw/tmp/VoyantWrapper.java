import java.io.*;
import java.util.*;

public class VoyantWrapper {
    public static void main(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println("Usage: VoyantWrapper <port> <webappPath> <adminPort>");
            System.exit(1);
        }
        
        int port = Integer.parseInt(args[0]);
        String webappPath = args[1];
        int adminPort = Integer.parseInt(args[2]);
        
        String javaHome = System.getProperty("java.home");
        String javaBin = javaHome + File.separator + "bin" + File.separator + "java";
        
        String webappLibs = webappPath + "/WEB-INF/lib/*";
        String voyantsJar = System.getProperty("java.class.path");
        String fullClasspath = webappLibs + ":" + voyantsJar;
        
        // Extended JVM flags for Java 17 - including JDT compiler modules
        List<String> jvmFlags = Arrays.asList(
            "--add-opens", "java.base/java.lang=ALL-UNNAMED",
            "--add-opens", "java.base/java.util=ALL-UNNAMED",
            "--add-opens", "java.base/java.io=ALL-UNNAMED",
            "--add-opens", "java.base/java.net=ALL-UNNAMED",
            "--add-opens", "java.base/sun.nio.ch=ALL-UNNAMED",
            "--add-opens", "java.base/java.lang.reflect=ALL-UNNAMED",
            "--add-opens", "java.base/java.text=ALL-UNNAMED",
            "--add-opens", "java.desktop/java.awt.font=ALL-UNNAMED",
            "--add-opens", "jdk.compiler/com.sun.tools.javac.code=ALL-UNNAMED",
            "--add-opens", "jdk.compiler/com.sun.tools.javac.comp=ALL-UNNAMED",
            "--add-opens", "jdk.compiler/com.sun.tools.javac.file=ALL-UNNAMED",
            "--add-opens", "jdk.compiler/com.sun.tools.javac.main=ALL-UNNAMED",
            "--add-opens", "jdk.compiler/com.sun.tools.javac.model=ALL-UNNAMED",
            "--add-opens", "jdk.compiler/com.sun.tools.javac.parser=ALL-UNNAMED",
            "--add-opens", "jdk.compiler/com.sun.tools.javac.processing=ALL-UNNAMED",
            "--add-opens", "jdk.compiler/com.sun.tools.javac.tree=ALL-UNNAMED",
            "--add-opens", "jdk.compiler/com.sun.tools.javac.util=ALL-UNNAMED",
            "--add-opens", "jdk.compiler/com.sun.tools.javac.jvm=ALL-UNNAMED",
            "--add-opens", "jdk.compiler/com.sun.tools.javac.api=ALL-UNNAMED"
        );
        
        List<String> cmd = new ArrayList<>();
        cmd.add(javaBin);
        cmd.addAll(jvmFlags);
        cmd.add("-Djava.io.tmpdir=/tmp/voyant");
        cmd.add("-cp");
        cmd.add(fullClasspath);
        cmd.add("org.aw20.jettydesktop.rte.JettyRunTime");
        cmd.add(String.valueOf(port));
        cmd.add(webappPath);
        cmd.add(String.valueOf(adminPort));
        
        System.out.println("Starting Voyant with Java 17: " + javaBin);
        
        ProcessBuilder pb = new ProcessBuilder(cmd);
        pb.redirectErrorStream(true);
        Process p = pb.start();
        
        try (BufferedReader br = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
            String line;
            while ((line = br.readLine()) != null) {
                System.out.println(line);
            }
        }
        
        int exitCode = p.waitFor();
        System.out.println("Voyant exited with code: " + exitCode);
    }
}