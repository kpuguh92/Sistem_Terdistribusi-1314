/*
 * To change this template, choose Tools | Templates
 * and open the template in the editor.
 */
package rmiclient;

import java.net.InetAddress;
import java.net.UnknownHostException;
import java.rmi.NotBoundException;
import java.rmi.RemoteException;
import rmi.MessageInterface;
import java.rmi.registry.LocateRegistry;
import java.rmi.registry.Registry;
import java.util.List;
import java.util.Scanner;
import java.util.logging.Level;
import java.util.logging.Logger;
/**
 *
 * @author Wildhan Ibrahim
 */
public class RmiClient {
    

    /**
     * @param args the command line arguments
     */
    public static void main(String[] args) {
        // TODO code application logic here
        Scanner scan;
        String params;
        InetAddress ipAddr;
        try{
            Registry regis = LocateRegistry.getRegistry("localhost", 9999);
            MessageInterface message = (MessageInterface) regis.lookup("Echo");
            ipAddr = InetAddress.getLocalHost();
            System.out.println("Masukan nomor propinsinya (00-33) : ");
            scan = new Scanner(System.in);
            params = scan.next();
            message.sender(ipAddr.getHostAddress(), params);
            List<String> listNews = message.rssReader(params);
            for (String s : listNews){
                System.out.println(s);
            }
        } catch (RemoteException | NotBoundException | UnknownHostException ex){
            System.out.println(ex.getMessage());
        }
        
    }
}
