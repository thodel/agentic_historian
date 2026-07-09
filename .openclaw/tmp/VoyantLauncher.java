import org.eclipse.jetty.server.*;
import org.eclipse.jetty.webapp.*;
import org.eclipse.jetty.servlet.*;
import org.eclipse.jetty.server.handler.*;
import org.eclipse.jetty.apache.jsp.*;
import javax.servlet.*;

public class VoyantLauncher {
    public static void main(String[] args) throws Exception {
        String webappPath = args.length > 0 ? args[0] : "/home/dh/voyant/VoyantServer2_4-M45/_app";
        int port = args.length > 1 ? Integer.parseInt(args[1]) : 8888;
        
        Server server = new Server(port);
        
        WebAppContext webapp = new WebAppContext();
        webapp.setContextPath("/");
        webapp.setWar(webappPath);
        
        // Enable parent-first classloading (critical for JSP to see java.lang.System)
        webapp.setParentLoaderPriority(true);
        
        // Let webapp load its own JSP servlet from WEB-INF/lib
        webapp.setAttribute("org.apache.catalina.jsp_classpath", System.getProperty("java.class.path"));
        
        server.setHandler(webapp);
        server.start();
        System.out.println("Voyant started on port " + port + ", webapp=" + webappPath);
        server.join();
    }
}