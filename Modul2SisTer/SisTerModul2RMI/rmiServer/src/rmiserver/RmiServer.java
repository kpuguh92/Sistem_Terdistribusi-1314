/*
 * To change this template, choose Tools | Templates
 * and open the template in the editor.
 */
package rmiserver;

import rmi.Message;
import java.rmi.registry.LocateRegistry;
import java.rmi.registry.Registry;
/**
 *
 * @author Wildhan Ibrahim
 */
public class RmiServer {

    /**
     * @param args the command line arguments
     */
    public static void main(String[] args) {
        // TODO code application logic here
        try {
            Registry regis = LocateRegistry.createRegistry(9999);
            regis.rebind("Echo", new Message());
            System.out.println("Server is Ready!!");
        } catch (Exception ex){
            System.out.println(ex.getMessage());
        }
    }
}
