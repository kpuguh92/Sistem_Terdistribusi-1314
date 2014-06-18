/*
 * To change this template, choose Tools | Templates
 * and open the template in the editor.
 */
package rmi;

import java.io.IOException;
import java.net.URL;
import java.net.URLConnection;
import java.rmi.RemoteException;
import java.rmi.server.UnicastRemoteObject;
import java.util.ArrayList;
import java.util.List;
import java.util.logging.Level;
import java.util.logging.Logger;
import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.parsers.ParserConfigurationException;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;
import org.xml.sax.SAXException;
/**
 *
 * @author Wildhan Ibrahim
 */
public class Message extends UnicastRemoteObject implements MessageInterface{
    
    public Message() throws RemoteException{
    }

    @Override
    public void sender(String addr, String request) throws RemoteException {
        System.out.println("From : "+addr+" Request : "+request);
    }

    @Override
    public List<String> rssReader(String params) throws RemoteException {
        List<String> result = new ArrayList<>();
        
        DocumentBuilderFactory dbFactory = DocumentBuilderFactory.newInstance();
        try {
            DocumentBuilder db = dbFactory.newDocumentBuilder();
            URL url = new URL("http://data.bmkg.go.id/propinsi_"+params+"_1.xml");
            URLConnection conn = url.openConnection();
            Document doc = db.parse(conn.getInputStream());
            
            NodeList nList = doc.getDocumentElement().getChildNodes();
            for (int i=0 ; i<nList.getLength() ; i++){
                Node node = nList.item(i);
                if (node instanceof Element) {
                    int ind = 0;
                    NodeList cnList = node.getChildNodes();
                    for (int j=0 ; j<cnList.getLength() ; j++){
                        Node cNode = cnList.item(j);
                        if (cNode instanceof Element){
                            
                            if (cNode.getNodeName().equals("Row")){
                                NodeList ccnList = cNode.getChildNodes();
                                
                                StringBuilder sb = new StringBuilder();
                                sb.append(++ind);
                                
                                result.add(sb.toString());
                                for (int k=0 ; k<ccnList.getLength() ; k++){
                                    Node ccNode = ccnList.item(k);
                                    if (!ccNode.getNodeName().equals("#text")){
                                        result.add(ccNode.getNodeName()+" : "+ccNode.getTextContent());
                                    }
                                }
                                result.add("");
                            } else {
                                result.add(cNode.getNodeName()+" : "+cNode.getTextContent());
                            }
                        }
                    }
                }
            }
        } catch (ParserConfigurationException | SAXException | IOException ex) {
            Logger.getLogger(Message.class.getName()).log(Level.SEVERE, null, ex);
        }
         
        return result;
    }
    
}
